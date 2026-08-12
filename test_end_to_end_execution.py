from dataclasses import replace
from decimal import Decimal

from app.autonomous_trading.candidate_to_proposal import (
    candidate_to_trade_proposal,
)
from app.autonomous_trading.decision_log import (
    log_trade_decision,
)
from app.autonomous_trading.execution_gate import (
    execute_approved_proposal,
)
from app.autonomous_trading.execution_state import (
    claim_decision_for_execution,
    mark_execution_complete,
)
from app.autonomous_trading.policy import (
    INITIAL_1000_POLICY,
)
from app.autonomous_trading.proposals import (
    TradeAction,
)
from app.autonomous_trading.risk_governor import (
    evaluate_trade_proposal,
)
from app.autonomous_trading.proposal_builder import (
    build_trade_proposal,
)
from app.market_db.portfolio_queries import (
    get_portfolio_summary,
)


portfolio_before = get_portfolio_summary()

policy = replace(
    INITIAL_1000_POLICY,
    name="controlled_execution_test",
    autonomous_execution_enabled=True,
)

proposal = build_trade_proposal(
    symbol="AAPL",
    action=TradeAction.BUY,
    quantity=Decimal("0.10"),
    confidence_percent=Decimal("85.00"),
    rationale="Controlled end-to-end paper execution test.",
    strategy_name="execution_test_v1",
)

decision = evaluate_trade_proposal(
    proposal=proposal,
    policy=policy,
    portfolio_summary=portfolio_before,
)

record = log_trade_decision(
    proposal=proposal,
    decision=decision,
    portfolio_id=portfolio_before["portfolio"]["id"],
)

claim = claim_decision_for_execution(
    decision_id=record.id,
)

if not claim.claimed:
    raise RuntimeError(claim.reason)

execution = execute_approved_proposal(
    decision_id=record.id,
    proposal=proposal,
    decision=decision,
    policy=policy,
    portfolio_id=portfolio_before["portfolio"]["id"],
)

if not execution.executed:
    raise RuntimeError(execution.reason)

transaction = execution.transaction

if transaction is None:
    raise RuntimeError(
        "Execution returned no transaction."
    )

transaction_id = transaction["id"]

mark_execution_complete(
    decision_id=record.id,
    portfolio_transaction_id=transaction_id,
)

portfolio_after = get_portfolio_summary()

print()
print("=== END-TO-END PAPER EXECUTION TEST ===")
print()
print("Decision ID:", record.id)
print("Risk approved:", decision.approved)
print("Execution enabled:", policy.autonomous_execution_enabled)
print("Claimed:", claim.claimed)
print("Executed:", execution.executed)
print("Transaction ID:", transaction_id)
print()
print("Cash before:", portfolio_before["cash_balance_usd"])
print("Cash after:", portfolio_after["cash_balance_usd"])
print("Positions after:", portfolio_after["position_count"])
print("Transactions after:", portfolio_after["transaction_count"])
print()
print("Transaction:")
print(transaction)
print()
print("IMPORTANT: This was a paper trade.")
