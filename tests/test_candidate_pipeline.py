from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app.autonomous_trading.strategy import (
    StrategyAction,
    create_strategy_candidate,
)
from app.capital.candidate_pipeline import (
    process_candidate,
)
from app.capital.policies import (
    MEAN_REVERSION_1000_POLICY,
)


candidate = create_strategy_candidate(
    symbol="AAPL",
    action=StrategyAction.BUY,
    confidence_percent=Decimal("80"),
    rationale="Controlled pipeline test.",
    suggested_position_percent=Decimal("10"),
    strategy_name="mean_reversion_v1",
)

proposal = SimpleNamespace()
decision = SimpleNamespace(
    approved=True,
    reasons=(),
)
record = SimpleNamespace(id=42)


locked_policy = replace(
    MEAN_REVERSION_1000_POLICY,
    autonomous_execution_enabled=False,
)


with (
    patch(
        "app.capital.candidate_pipeline."
        "candidate_to_trade_proposal",
        return_value=proposal,
    ),
    patch(
        "app.capital.candidate_pipeline."
        "evaluate_trade_proposal",
        return_value=decision,
    ),
    patch(
        "app.capital.candidate_pipeline."
        "log_trade_decision",
        return_value=record,
    ),
):
    locked = process_candidate(
        candidate=candidate,
        portfolio_id=3,
        portfolio_summary={"status": "success"},
        policy=locked_policy,
    )


enabled_policy = replace(
    MEAN_REVERSION_1000_POLICY,
    autonomous_execution_enabled=True,
)

claim = SimpleNamespace(
    claimed=True,
    execution_status="executing",
    reason="Claimed.",
)

execution = SimpleNamespace(
    executed=True,
    transaction_id=99,
    reason="Executed.",
)


with (
    patch(
        "app.capital.candidate_pipeline."
        "candidate_to_trade_proposal",
        return_value=proposal,
    ),
    patch(
        "app.capital.candidate_pipeline."
        "evaluate_trade_proposal",
        return_value=decision,
    ),
    patch(
        "app.capital.candidate_pipeline."
        "log_trade_decision",
        return_value=record,
    ),
    patch(
        "app.capital.candidate_pipeline."
        "claim_decision_for_execution",
        return_value=claim,
    ),
    patch(
        "app.capital.candidate_pipeline."
        "execute_approved_proposal",
        return_value=execution,
    ),
    patch(
        "app.capital.candidate_pipeline."
        "mark_execution_complete",
    ) as mark_complete,
):
    executed = process_candidate(
        candidate=candidate,
        portfolio_id=3,
        portfolio_summary={"status": "success"},
        policy=enabled_policy,
    )


assert locked.decision_logged is True
assert locked.risk_approved is True
assert locked.execution_attempted is False
assert locked.execution_status == "not_executed"

assert executed.execution_attempted is True
assert executed.execution_status == "executed"
assert executed.transaction_id == 99

mark_complete.assert_called_once_with(
    decision_id=42,
    portfolio_transaction_id=99,
)

print("locked_policy_path: PASS")
print("enabled_execution_path: PASS")
print("execution_completion_tracking: PASS")
print("database_writes: NONE")
