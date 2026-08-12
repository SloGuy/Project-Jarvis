from dataclasses import dataclass
from typing import Any

from app.autonomous_trading.policy import RiskPolicy
from app.autonomous_trading.proposals import (
    TradeAction,
    TradeProposal,
)
from app.autonomous_trading.risk_governor import (
    RiskDecision,
)
from app.market_db.paper_trading import (
    buy_asset,
    sell_asset,
)


@dataclass(frozen=True)
class ExecutionResult:
    executed: bool
    decision_id: int
    symbol: str
    action: TradeAction
    reason: str
    transaction_id: int | None = None
    transaction: dict[str, Any] | None = None


def execute_approved_proposal(
    *,
    decision_id: int,
    proposal: TradeProposal,
    decision: RiskDecision,
    policy: RiskPolicy,
    portfolio_id: int,
) -> ExecutionResult:
    """
    Execute one governor-approved proposal.

    The decision ID is embedded into the transaction metadata
    so execution recovery can deterministically identify whether
    a decision already produced a transaction.

    The transaction ID is extracted from the paper-trading
    response and exposed directly on ExecutionResult.

    Autonomous execution remains controlled by the active policy.
    """

    if not decision.approved:
        return ExecutionResult(
            executed=False,
            decision_id=decision_id,
            symbol=proposal.symbol,
            action=proposal.action,
            reason=(
                "Risk governor did not approve the proposal."
            ),
        )

    if not policy.autonomous_execution_enabled:
        return ExecutionResult(
            executed=False,
            decision_id=decision_id,
            symbol=proposal.symbol,
            action=proposal.action,
            reason=(
                "Autonomous execution is disabled "
                "by the active risk policy."
            ),
        )

    notes = (
        f"Autonomous decision ID: {decision_id}; "
        f"strategy: {proposal.strategy_name}; "
        f"policy: {policy.name}; "
        f"confidence: {proposal.confidence_percent}%; "
        f"rationale: {proposal.rationale}"
    )

    if proposal.action == TradeAction.BUY:
        transaction = buy_asset(
            symbol=proposal.symbol,
            quantity=proposal.quantity,
            portfolio_id=portfolio_id,
            fees_usd=0,
            notes=notes,
        )

    elif proposal.action == TradeAction.SELL:
        transaction = sell_asset(
            symbol=proposal.symbol,
            quantity=proposal.quantity,
            portfolio_id=portfolio_id,
            fees_usd=0,
            notes=notes,
        )

    else:
        return ExecutionResult(
            executed=False,
            decision_id=decision_id,
            symbol=proposal.symbol,
            action=proposal.action,
            reason="Unsupported trade action.",
        )

    transaction_data = transaction.get("transaction")

    if not transaction_data:
        raise RuntimeError(
            "Paper-trading execution returned no transaction data."
        )

    transaction_id = transaction_data.get("id")

    if transaction_id is None:
        raise RuntimeError(
            "Paper-trading execution returned no transaction ID."
        )

    return ExecutionResult(
        executed=True,
        decision_id=decision_id,
        symbol=proposal.symbol,
        action=proposal.action,
        reason="Paper trade executed successfully.",
        transaction_id=int(transaction_id),
        transaction=transaction_data,
    )
