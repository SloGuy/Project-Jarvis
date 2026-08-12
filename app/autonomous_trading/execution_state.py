from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import AutonomousTradeDecision


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ExecutionClaim:
    claimed: bool
    decision_id: int
    execution_status: str
    reason: str


def claim_decision_for_execution(
    *,
    decision_id: int,
) -> ExecutionClaim:
    """
    Atomically claim one approved autonomous decision for execution.

    A decision can only move from not_executed to executing once.
    Repeated attempts against the same decision are rejected.
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
            return ExecutionClaim(
                claimed=False,
                decision_id=decision_id,
                execution_status="not_found",
                reason="Autonomous decision was not found.",
            )

        if not decision.approved:
            return ExecutionClaim(
                claimed=False,
                decision_id=decision.id,
                execution_status=decision.execution_status,
                reason=(
                    "Risk-rejected decisions cannot be executed."
                ),
            )

        if decision.execution_status != "not_executed":
            return ExecutionClaim(
                claimed=False,
                decision_id=decision.id,
                execution_status=decision.execution_status,
                reason=(
                    "Decision has already been claimed "
                    "or processed for execution."
                ),
            )

        decision.execution_status = "executing"
        decision.execution_attempted_at = utc_now()
        decision.execution_error = None

        session.flush()

        return ExecutionClaim(
            claimed=True,
            decision_id=decision.id,
            execution_status="executing",
            reason="Decision successfully claimed for execution.",
        )


def mark_execution_failed(
    *,
    decision_id: int,
    error_message: str,
) -> None:
    with SessionLocal.begin() as session:
        decision = session.scalar(
            select(AutonomousTradeDecision)
            .where(
                AutonomousTradeDecision.id == decision_id,
            )
            .with_for_update()
        )

        if decision is None:
            raise ValueError(
                f"Autonomous decision {decision_id} was not found."
            )

        decision.execution_status = "failed"
        decision.execution_error = error_message.strip()


def mark_execution_complete(
    *,
    decision_id: int,
    portfolio_transaction_id: int,
) -> None:
    with SessionLocal.begin() as session:
        decision = session.scalar(
            select(AutonomousTradeDecision)
            .where(
                AutonomousTradeDecision.id == decision_id,
            )
            .with_for_update()
        )

        if decision is None:
            raise ValueError(
                f"Autonomous decision {decision_id} was not found."
            )

        if decision.execution_status != "executing":
            raise ValueError(
                "Decision is not currently claimed for execution."
            )

        decision.execution_status = "executed"
        decision.executed_at = utc_now()
        decision.portfolio_transaction_id = (
            portfolio_transaction_id
        )
        decision.execution_error = None
