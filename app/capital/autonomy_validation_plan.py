"""Application-controlled MR2/BTC provider-time validation drafts."""

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY as POLICY
from app.capital.replay_manifest import capture_replay_manifest
from app.capital.research_models import ResearchStatus
from app.capital.validation_input_contract import make_input_contract
from app.capital.validation_plan import (
    BENCHMARK,
    research_snapshot,
    timestamp,
    validate_plan,
)


WINDOW = timedelta(days=6)
MINIMUM_LEAD = timedelta(minutes=30)

CRITERIA = {
    "minimum_completed_trades": 30,
    "maximum_stale_tick_percent": "5",
    "maximum_unusable_regular_tick_percent": "10",
    "minimum_return_percent": "0",
    "minimum_excess_return_percent": "0",
    "maximum_drawdown_percent": "5",
}


def encode(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def require_research_candidate(*, research_id):
    from app.capital.research_service import (
        require_research_candidate as read_candidate,
    )

    return read_candidate(research_id=research_id)


def require_strategy(*, strategy_name):
    from app.capital.strategy_registry import (
        require_strategy as read_strategy,
    )

    return read_strategy(strategy_name=strategy_name)


def read_btc_asset_id():
    from sqlalchemy import select
    from app.market_db.database import SessionLocal
    from app.market_db.models import MarketAsset

    with SessionLocal() as session:
        asset = session.scalar(
            select(MarketAsset).where(
                MarketAsset.symbol == "BTC",
                MarketAsset.asset_type == "crypto",
            )
        )
        if asset is None or asset.is_active is not True:
            raise ValueError("An active BTC asset is required.")
        return asset.id


def build_validation_draft(*, research_id, start, now=None):
    """Build a future draft without registration or state changes.

    Persisted drafts retain their original version on retry.
    A six-day window does not guarantee sufficient trades.
    """
    measured = now if now is not None else datetime.now(timezone.utc)

    if not isinstance(measured, datetime) or measured.utcoffset() is None:
        raise ValueError("Current time must be timezone-aware.")

    measured = measured.astimezone(timezone.utc)
    beginning = timestamp(start)

    if beginning - measured < MINIMUM_LEAD:
        raise ValueError(
            "Validation requires at least 30 minutes of lead time."
        )

    if beginning.second or beginning.microsecond:
        raise ValueError(
            "Validation must start on a whole-minute boundary."
        )

    candidate = require_research_candidate(research_id=research_id)

    if candidate.status not in {
        ResearchStatus.PROPOSED,
        ResearchStatus.SCREENING,
        ResearchStatus.RESEARCHING,
        ResearchStatus.READY_FOR_EXPERIMENT,
    }:
        raise ValueError(
            "Research is not eligible for new validation work."
        )

    universe = [
        symbol.strip().upper()
        for symbol in candidate.asset_universe
    ]

    if universe != ["BTC"]:
        raise ValueError(
            "Automated validation currently supports BTC only."
        )

    strategy = require_strategy(
        strategy_name=candidate.strategy_name
    )

    if (
        strategy.name != "mean_reversion_v2"
        or strategy.enabled is not True
        or strategy.implementation_module
        != "app.autonomous_trading.mean_reversion_v2_strategy"
        or strategy.evaluator_name
        != "evaluate_mean_reversion_v2_strategy"
    ):
        raise ValueError(
            "Automated validation requires the supported MR2 runner."
        )

    policy = json.loads(
        json.dumps(asdict(POLICY), default=encode)
    )

    draft = {
        "schema_version": 2,
        "designation": "prospective_validation",
        "created_by": "capital.validation",
        "research": research_snapshot(candidate),
        "strategy_version": strategy.version,
        "asset_id": read_btc_asset_id(),
        "symbol": "BTC",
        "provider": "CoinGecko",
        "start": beginning.isoformat(),
        "end_exclusive": (beginning + WINDOW).isoformat(),
        "fee_bps": "5",
        "slippage_bps": "5",
        "benchmark": BENCHMARK,
        "criteria": deepcopy(CRITERIA),
        "policy": policy,
        "execution_manifest": capture_replay_manifest(),
        "input_contract": make_input_contract(policy=policy),
    }

    # Registration assigns the actual creation timestamp.
    validate_plan({
        **deepcopy(draft),
        "created_at": measured.isoformat(),
    })
    return draft
