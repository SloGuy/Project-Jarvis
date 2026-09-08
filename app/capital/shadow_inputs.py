"""Historical inputs and allocation limits for the two-strategy shadow."""
from dataclasses import replace
from datetime import timezone
from decimal import Decimal as D

from sqlalchemy import select

from app.market_db.models import MarketAsset, PriceObservation
from app.capital.validation_access import historical_access
from app.capital.historical_observations import historical_window_statement
from app.capital.mean_reversion_math import (
    calculate_mean_reversion_snapshot,
    LOOKBACK_OBSERVATIONS as MEAN_LOOKBACK,
)
from app.autonomous_trading.volatility_breakout_strategy import (
    calculate_volatility_breakout_snapshot,
    LOOKBACK_OBSERVATIONS as BREAKOUT_LOOKBACK,
)
from app.capital.allocation_policy import CAPITAL_V2_SHADOW_POLICY
from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY
from app.capital.shadow_coordinator import EVALUATORS


def load_shadow_inputs(session, *, asset_id, provider, decision_at):
    # Reuse provider, identity and strict cutoff validation.
    statement = historical_window_statement(
        asset_id=asset_id, provider=provider, decision_at=decision_at
    ).limit(max(MEAN_LOOKBACK, BREAKOUT_LOOKBACK))
    with historical_access(
        asset_id=asset_id, provider=provider, decision_at=decision_at
    ) as check_times, session.no_autoflush:
        asset = session.execute(
            select(MarketAsset.symbol, MarketAsset.asset_type)
            .where(MarketAsset.id == asset_id)
        ).one_or_none()
        if asset is None:
            raise ValueError("Unknown shadow asset.")
        expected_type = {"Finnhub": "stock", "CoinGecko": "crypto"}
        if asset.asset_type != expected_type[provider]:
            raise ValueError("Asset type and provider differ.")
        rows = session.execute(statement).all()
        times = [
            row.observed_at.astimezone(timezone.utc)
            if row.observed_at.utcoffset() is not None
            else row.observed_at.replace(tzinfo=timezone.utc)
            for row in rows
        ]
        check_times(times)

    observations = [
        {"price_usd": float(row.price_usd), "observed_at": at.isoformat()}
        for row, at in zip(rows, times)
    ]
    mean = calculate_mean_reversion_snapshot(
        symbol=asset.symbol, observations=observations[:MEAN_LOOKBACK]
    )
    breakout = calculate_volatility_breakout_snapshot(
        symbol=asset.symbol, observations=observations[:BREAKOUT_LOOKBACK]
    )
    quote = None
    if rows:
        price = D(str(rows[0].price_usd))
        if not price.is_finite() or price <= 0:
            raise ValueError("Invalid latest shadow observation.")
        # Match the existing snapshot adapter's float conversion.
        quote = (D(str(float(price))), times[0])
    return {
        "symbol": asset.symbol,
        "snapshots": {
            ("mean_reversion_v2", asset.symbol): mean,
            ("volatility_breakout_v1", asset.symbol): breakout,
        },
        "quote": quote,
        "observation_ids": [row.id for row in rows],
        "observation_times": [at.isoformat() for at in times],
        "availability_verified": False,
    }


def finite(value):
    result = D(str(value))
    if not result.is_finite():
        raise ValueError("Allocation values must be finite.")
    return result


def shadow_configuration(allocation):
    policy = CAPITAL_V2_SHADOW_POLICY
    if (
        allocation.get("status") != "success"
        or allocation.get("mode") != "shadow"
        or allocation.get("paper_portfolio_writes") is not False
        or allocation.get("paper_execution_authority") is not False
        or allocation.get("live_capital_authority") is not False
    ):
        raise ValueError("Expected a successful non-executing shadow allocation.")
    if allocation.get("policy_name") != policy.name:
        raise ValueError("Unsupported allocation policy.")
    capital = finite(allocation["notional_capital_usd"])
    if capital != policy.notional_capital_usd:
        raise ValueError("Allocation capital differs from policy.")
    caps, seen, total = {}, set(), D("0")
    for recommendation in allocation["recommendations"]:
        strategy = recommendation["strategy_name"]
        if strategy in seen:
            raise ValueError("Duplicate strategy allocation.")
        seen.add(strategy)
        cap = finite(recommendation["recommended_allocation_percent"])
        evidence_cap = finite(recommendation["evidence_cap_percent"])
        label_caps = {
            "insufficient": policy.insufficient_evidence_cap_percent,
            "developing": policy.developing_evidence_cap_percent,
            "substantial": policy.substantial_evidence_cap_percent,
        }
        allowed = min(
            label_caps.get(recommendation["evidence_label"], D("0")),
            policy.maximum_strategy_allocation_percent,
        )
        if not 0 <= cap <= evidence_cap <= allowed:
            raise ValueError("Recommendation exceeds its evidence or policy cap.")
        if cap > 0 and (
            recommendation.get("eligible") is not True
            or recommendation["committee_decision"] not in {"continue", "promote"}
        ):
            raise ValueError("Ineligible strategy received an allocation.")
        total += cap
        if strategy in EVALUATORS:
            caps[strategy] = cap
    if (
        total != finite(allocation["deployed_percent"])
        or total > policy.maximum_total_allocation_percent
        or finite(allocation["cash_reserve_percent"]) != 100 - total
        or 100 - total < policy.minimum_cash_reserve_percent
    ):
        raise ValueError("Allocation totals or cash reserve violate policy.")
    if set(caps) != set(EVALUATORS):
        raise ValueError("Both supported strategies must have explicit recommendations.")
    risk_policy = replace(
        MEAN_REVERSION_V2_1000_POLICY,
        starting_capital_usd=capital,
        max_total_exposure_percent=min(
            MEAN_REVERSION_V2_1000_POLICY.max_total_exposure_percent,
            policy.maximum_total_allocation_percent,
        ),
        minimum_cash_reserve_percent=max(
            MEAN_REVERSION_V2_1000_POLICY.minimum_cash_reserve_percent,
            policy.minimum_cash_reserve_percent,
        ),
    )
    return risk_policy, caps
