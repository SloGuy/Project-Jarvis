from datetime import datetime
from decimal import Decimal
from typing import Any

from app.autonomous_trading.candidate_to_proposal import (
    candidate_to_trade_proposal,
)
from app.autonomous_trading.mean_reversion_strategy import (
    STRATEGY_NAME,
    evaluate_mean_reversion_strategy,
    get_mean_reversion_snapshot,
)
from app.autonomous_trading.risk_governor import (
    evaluate_trade_proposal,
)
from app.autonomous_trading.strategy import (
    PositionContext,
    StrategyAction,
)
from app.capital.policies import (
    MEAN_REVERSION_1000_POLICY,
)
from app.capital.portfolio_service import (
    get_or_create_mean_reversion_portfolio,
)
from app.market_db.portfolio_queries import (
    get_portfolio_summary,
)
from app.market_universe import (
    get_crypto_provider_map,
    get_deep_snapshot_stock_symbols,
)


def get_mean_reversion_universe() -> tuple[str, ...]:
    stocks = get_deep_snapshot_stock_symbols()
    crypto = tuple(
        get_crypto_provider_map().values()
    )

    return tuple(
        dict.fromkeys(
            (*stocks, *crypto)
        )
    )


def _position_context(
    *,
    symbol: str,
    position: dict[str, Any] | None,
) -> PositionContext:
    if position is None:
        return PositionContext(
            symbol=symbol,
            quantity=Decimal("0"),
            average_cost_usd=Decimal("0"),
            market_value_usd=Decimal("0"),
            allocation_percent=Decimal("0"),
            unrealized_gain_loss_usd=Decimal("0"),
            unrealized_gain_loss_percent=Decimal("0"),
            opened_at=None,
        )

    return PositionContext(
        symbol=symbol,
        quantity=Decimal(
            str(position.get("quantity") or 0)
        ),
        average_cost_usd=Decimal(
            str(position.get("average_cost_usd") or 0)
        ),
        market_value_usd=Decimal(
            str(position.get("market_value_usd") or 0)
        ),
        allocation_percent=Decimal(
            str(position.get("allocation_percent") or 0)
        ),
        unrealized_gain_loss_usd=Decimal(
            str(
                position.get(
                    "unrealized_gain_loss_usd"
                )
                or 0
            )
        ),
        unrealized_gain_loss_percent=Decimal(
            str(
                position.get(
                    "unrealized_gain_loss_percent"
                )
                or 0
            )
        ),
        opened_at=(
            datetime.fromisoformat(
                str(position["created_at"]).replace(
                    "Z",
                    "+00:00",
                )
            )
            if position.get("created_at")
            else None
        ),
    )


def run_mean_reversion_dry_cycle() -> dict[str, Any]:
    portfolio_record = (
        get_or_create_mean_reversion_portfolio()
    )

    portfolio = get_portfolio_summary(
        portfolio_id=portfolio_record.id,
    )

    if portfolio.get("status") != "success":
        raise RuntimeError(
            "Mean-reversion portfolio is unavailable."
        )

    positions_by_symbol = {
        str(position["symbol"]).upper(): position
        for position in portfolio.get("positions", [])
    }

    symbols = get_mean_reversion_universe()
    results: list[dict[str, Any]] = []

    for symbol in symbols:
        normalized_symbol = symbol.upper()

        context = _position_context(
            symbol=normalized_symbol,
            position=positions_by_symbol.get(
                normalized_symbol
            ),
        )

        snapshot = get_mean_reversion_snapshot(
            symbol=normalized_symbol,
        )

        candidate = evaluate_mean_reversion_strategy(
            symbol=normalized_symbol,
            position_context=context,
            snapshot=snapshot,
        )

        result = {
            "symbol": normalized_symbol,
            "action": candidate.action.value,
            "confidence_percent": float(
                candidate.confidence_percent
            ),
            "rationale": candidate.rationale,
            "snapshot_usable": snapshot.usable,
            "observation_count": (
                snapshot.observation_count
            ),
            "z_score": (
                float(snapshot.z_score)
                if snapshot.z_score is not None
                else None
            ),
            "proposal_created": False,
            "risk_approved": None,
            "risk_reasons": [],
            "execution_attempted": False,
        }

        if candidate.action != StrategyAction.HOLD:
            proposal = candidate_to_trade_proposal(
                candidate=candidate,
                portfolio_id=portfolio_record.id,
            )

            if proposal is not None:
                decision = evaluate_trade_proposal(
                    proposal=proposal,
                    policy=MEAN_REVERSION_1000_POLICY,
                    portfolio_summary=portfolio,
                )

                result["proposal_created"] = True
                result["risk_approved"] = (
                    decision.approved
                )
                result["risk_reasons"] = list(
                    decision.reasons
                )

        results.append(result)

    return {
        "status": "success",
        "mode": "dry_run",
        "strategy": STRATEGY_NAME,
        "policy": MEAN_REVERSION_1000_POLICY.name,
        "portfolio_id": portfolio_record.id,
        "autonomous_execution_enabled": (
            MEAN_REVERSION_1000_POLICY
            .autonomous_execution_enabled
        ),
        "asset_count": len(symbols),
        "usable_snapshot_count": sum(
            1
            for result in results
            if result["snapshot_usable"]
        ),
        "actionable_count": sum(
            1
            for result in results
            if result["proposal_created"]
        ),
        "approved_count": sum(
            1
            for result in results
            if result["risk_approved"] is True
        ),
        "execution_attempted_count": 0,
        "results": results,
    }
