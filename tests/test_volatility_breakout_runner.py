from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app.autonomous_trading.strategy import (
    StrategyAction,
    create_strategy_candidate,
)
from app.autonomous_trading.volatility_breakout_strategy import (
    VolatilityBreakoutSnapshot,
)
from app.capital.volatility_breakout_paper_runner import (
    run_volatility_breakout_paper_cycle,
)


NOW = datetime(
    2026,
    9,
    3,
    tzinfo=timezone.utc,
)

snapshot = VolatilityBreakoutSnapshot(
    symbol="AAPL",
    observation_at=NOW,
    latest_price_usd=Decimal("102"),
    compressed_range_high_usd=Decimal("101"),
    compressed_range_low_usd=Decimal("99"),
    compression_ratio=Decimal("0.50"),
    breakout_percent=Decimal("0.99"),
    expansion_ratio=Decimal("3.00"),
    exit_average_usd=Decimal("100"),
    observation_count=60,
    usable=True,
    reason=None,
)

candidate = create_strategy_candidate(
    symbol="AAPL",
    action=StrategyAction.BUY,
    confidence_percent=Decimal("75"),
    rationale="Synthetic confirmed breakout.",
    suggested_position_percent=Decimal("10"),
    strategy_name="volatility_breakout_v1",
)

portfolio = {
    "status": "success",
    "total_value_usd": 1000.0,
    "cash_balance_usd": 1000.0,
    "positions": [],
}

pipeline = SimpleNamespace(
    proposal_created=True,
    decision_logged=True,
    risk_approved=True,
    risk_reasons=(),
    decision_id=101,
    execution_attempted=True,
    execution_status="executed",
    execution_reason="Synthetic paper execution.",
    transaction_id=202,
)

with (
    patch(
        "app.capital."
        "volatility_breakout_paper_runner."
        "get_or_create_volatility_breakout_portfolio",
        return_value=SimpleNamespace(id=4),
    ),
    patch(
        "app.capital."
        "volatility_breakout_paper_runner."
        "get_portfolio_summary",
        return_value=portfolio,
    ),
    patch(
        "app.capital."
        "volatility_breakout_paper_runner."
        "get_volatility_breakout_universe",
        return_value=("AAPL",),
    ),
    patch(
        "app.capital."
        "volatility_breakout_paper_runner."
        "get_volatility_breakout_snapshot",
        return_value=snapshot,
    ),
    patch(
        "app.capital."
        "volatility_breakout_paper_runner."
        "evaluate_volatility_breakout_strategy",
        return_value=candidate,
    ),
    patch(
        "app.capital."
        "volatility_breakout_paper_runner."
        "evaluate_exit_rules",
        return_value=SimpleNamespace(
            should_exit=False,
            rule=None,
            rationale="No risk exit.",
        ),
    ),
    patch(
        "app.capital."
        "volatility_breakout_paper_runner."
        "process_candidate",
        return_value=pipeline,
    ),
    patch(
        "app.capital."
        "volatility_breakout_paper_runner."
        "open_trade_journal",
    ) as open_journal,
    patch(
        "app.capital."
        "volatility_breakout_paper_runner."
        "close_trade_journal",
    ) as close_journal,
):
    result = run_volatility_breakout_paper_cycle()


assert result["status"] == "success"
assert result["strategy"] == "volatility_breakout_v1"
assert result["portfolio_id"] == 4
assert result["asset_count"] == 1
assert result["actionable_count"] == 1
assert result["approved_count"] == 1
assert result["executed_count"] == 1
assert result["execution_failed_count"] == 0

open_journal.assert_called_once()
close_journal.assert_not_called()

assert (
    open_journal.call_args.kwargs[
        "entry_market_context"
    ]["compression_ratio"]
    == 0.5
)

print("paper_pipeline_wiring: PASS")
print("entry_journal_wiring: PASS")
print("isolated_portfolio: PASS")
print("real_database_writes: NONE")
