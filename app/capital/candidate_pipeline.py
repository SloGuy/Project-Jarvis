from dataclasses import dataclass

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
    mark_execution_failed,
)
from app.autonomous_trading.policy import RiskPolicy
from app.autonomous_trading.risk_governor import (
    evaluate_trade_proposal,
)
from app.autonomous_trading.strategy import (
    StrategyAction,
    StrategyCandidate,
)


@dataclass(frozen=True)
class CandidatePipelineResult:
    proposal_created: bool
    decision_logged: bool
    risk_approved: bool | None
    risk_reasons: tuple[str, ...]
    decision_id: int | None
    execution_attempted: bool
    execution_status: str
    execution_reason: str
    transaction_id: int | None


def process_candidate(
    *,
    candidate: StrategyCandidate,
    portfolio_id: int,
    portfolio_summary: dict,
    policy: RiskPolicy,
) -> CandidatePipelineResult:
    if candidate.action == StrategyAction.HOLD:
        return CandidatePipelineResult(
            proposal_created=False,
            decision_logged=False,
            risk_approved=None,
            risk_reasons=(),
            decision_id=None,
            execution_attempted=False,
            execution_status="not_applicable",
            execution_reason="Strategy returned HOLD.",
            transaction_id=None,
        )

    proposal = candidate_to_trade_proposal(
        candidate=candidate,
        portfolio_id=portfolio_id,
    )

    if proposal is None:
        raise RuntimeError(
            "Actionable candidate produced no proposal."
        )

    decision = evaluate_trade_proposal(
        proposal=proposal,
        policy=policy,
        portfolio_summary=portfolio_summary,
    )

    record = log_trade_decision(
        proposal=proposal,
        decision=decision,
        portfolio_id=portfolio_id,
    )

    if not decision.approved:
        return CandidatePipelineResult(
            proposal_created=True,
            decision_logged=True,
            risk_approved=False,
            risk_reasons=decision.reasons,
            decision_id=record.id,
            execution_attempted=False,
            execution_status="not_executed",
            execution_reason=(
                "Risk governor rejected the proposal."
            ),
            transaction_id=None,
        )

    if not policy.autonomous_execution_enabled:
        return CandidatePipelineResult(
            proposal_created=True,
            decision_logged=True,
            risk_approved=True,
            risk_reasons=(),
            decision_id=record.id,
            execution_attempted=False,
            execution_status="not_executed",
            execution_reason=(
                "Autonomous execution is disabled "
                "by the active risk policy."
            ),
            transaction_id=None,
        )

    claim = claim_decision_for_execution(
        decision_id=record.id,
    )

    if not claim.claimed:
        return CandidatePipelineResult(
            proposal_created=True,
            decision_logged=True,
            risk_approved=True,
            risk_reasons=(),
            decision_id=record.id,
            execution_attempted=False,
            execution_status=claim.execution_status,
            execution_reason=claim.reason,
            transaction_id=None,
        )

    try:
        execution = execute_approved_proposal(
            decision_id=record.id,
            proposal=proposal,
            decision=decision,
            policy=policy,
            portfolio_id=portfolio_id,
        )

        if not execution.executed:
            mark_execution_failed(
                decision_id=record.id,
                error_message=execution.reason,
            )

            return CandidatePipelineResult(
                proposal_created=True,
                decision_logged=True,
                risk_approved=True,
                risk_reasons=(),
                decision_id=record.id,
                execution_attempted=True,
                execution_status="failed",
                execution_reason=execution.reason,
                transaction_id=None,
            )

        if execution.transaction_id is None:
            raise RuntimeError(
                "Execution returned no transaction ID."
            )

        mark_execution_complete(
            decision_id=record.id,
            portfolio_transaction_id=(
                execution.transaction_id
            ),
        )

        return CandidatePipelineResult(
            proposal_created=True,
            decision_logged=True,
            risk_approved=True,
            risk_reasons=(),
            decision_id=record.id,
            execution_attempted=True,
            execution_status="executed",
            execution_reason=execution.reason,
            transaction_id=execution.transaction_id,
        )

    except Exception as exc:
        mark_execution_failed(
            decision_id=record.id,
            error_message=str(exc),
        )

        return CandidatePipelineResult(
            proposal_created=True,
            decision_logged=True,
            risk_approved=True,
            risk_reasons=(),
            decision_id=record.id,
            execution_attempted=True,
            execution_status="failed",
            execution_reason=str(exc),
            transaction_id=None,
        )
