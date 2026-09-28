"""Application-controlled MR2/BTC validation drafts."""

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json

from sqlalchemy import select

from app.capital.replay_manifest import capture_replay_manifest
from app.capital.research_models import ResearchStatus
from app.capital.research_service import require_research_candidate
from app.capital.run_evaluation import POLICY, encode
from app.capital.strategy_registry import require_strategy
from app.capital.validation_plan import (
    BENCHMARK,
    research_snapshot,
    timestamp,
    validate_plan,
)
from app.market_db.database import SessionLocal
from app.market_db.models import MarketAsset


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


def build_validation_draft(*, research_id, start, now=None):
    """Build a future draft without registration or state changes.

    The scheduler persists this draft before registration and reuses it
    on retry. A six-day window does not guarantee sufficient trades.
    Passing this builder does not establish hypothesis quality.
    """
    measured = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(measured, datetime) or measured.utcoffset() is None:
        raise ValueError("Current time must be timezone-aware.")
    measured = measured.astimezone(timezone.utc)

    beginning = timestamp(start)
    if beginning - measured < MINIMUM_LEAD:
        raise ValueError("Validation requires at least 30 minutes of lead time.")
    if beginning.second or beginning.microsecond:
        raise ValueError("Validation must start on a whole-minute boundary.")

    candidate = require_research_candidate(research_id=research_id)
    if candidate.status not in {
        ResearchStatus.PROPOSED,
        ResearchStatus.SCREENING,
        ResearchStatus.RESEARCHING,
        ResearchStatus.READY_FOR_EXPERIMENT,
    }:
        raise ValueError("Research is not eligible for new validation work.")

    universe = [
        symbol.strip().upper() for symbol in candidate.asset_universe
    ]
    if universe != ["BTC"]:
        raise ValueError("Automated validation currently supports BTC only.")

    strategy = require_strategy(strategy_name=candidate.strategy_name)
    if (
        strategy.name != "mean_reversion_v2"
        or strategy.enabled is not True
        or strategy.implementation_module
        != "app.autonomous_trading.mean_reversion_v2_strategy"
        or strategy.evaluator_name != "evaluate_mean_reversion_v2_strategy"
    ):
        raise ValueError("Automated validation requires the supported MR2 runner.")

    with SessionLocal() as session:
        asset = session.scalar(
            select(MarketAsset).where(
                MarketAsset.symbol == "BTC",
                MarketAsset.asset_type == "crypto",
            )
        )
        if asset is None or asset.is_active is not True:
            raise ValueError("An active BTC asset is required.")
        asset_id = asset.id

    draft = {
        "schema_version": 1,
        "designation": "prospective_validation",
        "created_by": "capital.validation",
        "research": research_snapshot(candidate),
        "strategy_version": strategy.version,
        "asset_id": asset_id,
        "symbol": "BTC",
        "provider": "CoinGecko",
        "start": beginning.isoformat(),
        "end_exclusive": (beginning + WINDOW).isoformat(),
        "fee_bps": "5",
        "slippage_bps": "5",
        "benchmark": BENCHMARK,
        "criteria": deepcopy(CRITERIA),
        "policy": json.loads(json.dumps(asdict(POLICY), default=encode)),
        "execution_manifest": capture_replay_manifest(),
    }

    # Validate using a temporary creation timestamp. The registry assigns
    # the actual timestamp when registration occurs.
    validate_plan({**deepcopy(draft), "created_at": measured.isoformat()})
    return draft
