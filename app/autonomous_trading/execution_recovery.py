from dataclasses import dataclass

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import (
    AutonomousTradeDecision,
    PortfolioTransaction,
)


@dataclass(frozen=True)
class RecoveryResult:
    decision_id: int
    status: str
    transaction_id: int | None
    message: str


def reconcile_execution(
    *,
    decision_id: int,
) -> RecoveryResult:
    """
    Reconcile one autonomous decision that is currently
    marked as executing.

    This function never creates a trade.

    If a transaction containing the exact decision ID is found,
    the decision is marked executed.

    If no matching transaction exists, the decision remains
    executing so that a higher-level recovery policy can decide
    what to do next.
    """

    with SessionLocal.begin() as session:
        decision = session.scalar(
            select(AutonomousTradeDecision)
            .where(
                AutonomousTradeDecision.id == decision_id,
            )
            .with_for_update()
        )

        if decision is None:
            return RecoveryResult(
                decision_id=decision_id,
                status="not_found",
                transaction_id=None,
                message=(
                    "Autonomous decision was not found."
                ),
            )

        if decision.execution_status == "executed":
            return RecoveryResult(
                decision_id=decision.id,
                status="already_executed",
                transaction_id=(
                    decision.portfolio_transaction_id
                ),
                message=(
                    "Decision is already marked executed."
                ),
            )

        if decision.execution_status != "executing":
            return RecoveryResult(
                decision_id=decision.id,
                status="not_recoverable",
                transaction_id=None,
                message=(
                    "Decision is not currently in the "
                    "executing state."
                ),
            )

        decision_marker = (
            f"Autonomous decision ID: {decision.id};"
        )

        transaction = session.scalar(
            select(PortfolioTransaction)
            .where(
                PortfolioTransaction.portfolio_id
                == decision.portfolio_id,
                PortfolioTransaction.asset_id
                == decision.asset_id,
                PortfolioTransaction.transaction_type
                == decision.action,
                PortfolioTransaction.notes.like(
                    f"{decision_marker}%"
                ),
            )
            .order_by(
                PortfolioTransaction.created_at.desc(),
                PortfolioTransaction.id.desc(),
            )
            .limit(1)
        )

        if transaction is None:
            return RecoveryResult(
                decision_id=decision.id,
                status="still_executing",
                transaction_id=None,
                message=(
                    "No matching paper transaction was found. "
                    "Decision remains executing."
                ),
            )

        decision.execution_status = "executed"
        decision.executed_at = transaction.created_at
        decision.portfolio_transaction_id = transaction.id
        decision.execution_error = None

        return RecoveryResult(
            decision_id=decision.id,
            status="reconciled",
            transaction_id=transaction.id,
            message=(
                "Matching paper transaction found. "
                "Decision marked executed."
            ),
        )


def reconcile_pending_executions() -> list[RecoveryResult]:
    """
    Reconcile every autonomous decision currently stuck in
    the executing state.

    This function never creates or retries trades.
    """

    with SessionLocal() as session:
        decision_ids = session.scalars(
            select(AutonomousTradeDecision.id)
            .where(
                AutonomousTradeDecision.execution_status
                == "executing",
            )
            .order_by(
                AutonomousTradeDecision.id.asc()
            )
        ).all()

    return [
        reconcile_execution(
            decision_id=decision_id,
        )
        for decision_id in decision_ids
    ]
