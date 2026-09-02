from datetime import datetime, timezone
from typing import Any

from app.capital.committee_models import (
    CommitteeDecision,
    CommitteeReport,
    GateStatus,
)
from app.capital.experiment_registry import (
    require_experiment,
)
from app.capital.graduation_gates import (
    build_graduation_gates,
)
from app.capital.performance_lab import (
    get_performance_lab,
)


def utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _committee_decision(
    *,
    failed_count: int,
    pending_count: int,
) -> CommitteeDecision:
    if failed_count >= 3:
        return CommitteeDecision.KILL

    if failed_count > 0:
        return CommitteeDecision.REVISE

    if pending_count > 0:
        return CommitteeDecision.CONTINUE

    return CommitteeDecision.PROMOTE


def _decision_rationale(
    *,
    decision: CommitteeDecision,
    failed_count: int,
    pending_count: int,
) -> str:
    if decision == CommitteeDecision.PROMOTE:
        return (
            "All hard graduation gates passed. "
            "The strategy is eligible for a separate "
            "human-reviewed stage promotion."
        )

    if decision == CommitteeDecision.KILL:
        return (
            f"{failed_count} mature graduation gates "
            "failed. Stop the experiment and preserve "
            "its evidence for postmortem review."
        )

    if decision == CommitteeDecision.REVISE:
        return (
            f"{failed_count} mature graduation gate(s) "
            "failed. Revise the hypothesis or strategy "
            "before further promotion review."
        )

    return (
        f"{pending_count} graduation gate(s) remain "
        "pending. Continue isolated paper evidence "
        "collection without changing live-capital "
        "authority."
    )


def evaluate_strategy_committee(
    *,
    strategy_performance: dict[str, Any],
) -> CommitteeReport:
    experiment = require_experiment(
        experiment_id=strategy_performance[
            "experiment_id"
        ]
    )

    gates = build_graduation_gates(
        experiment=experiment,
        performance=strategy_performance,
    )

    passed = tuple(
        gate
        for gate in gates
        if gate.status == GateStatus.PASSED
    )
    failed = tuple(
        gate
        for gate in gates
        if gate.status == GateStatus.FAILED
    )
    pending = tuple(
        gate
        for gate in gates
        if gate.status == GateStatus.PENDING
    )

    decision = _committee_decision(
        failed_count=len(failed),
        pending_count=len(pending),
    )

    strengths = tuple(
        gate.gate_name
        for gate in passed
    )

    concerns = tuple(
        (
            f"{gate.gate_name}: "
            f"{gate.rationale}"
        )
        for gate in (
            failed + pending
        )
    )

    return CommitteeReport(
        strategy_name=strategy_performance[
            "strategy_name"
        ],
        experiment_id=strategy_performance[
            "experiment_id"
        ],
        portfolio_id=int(
            strategy_performance[
                "portfolio_id"
            ]
        ),
        decision=decision,
        generated_at=utc_now_iso(),
        gate_results=gates,
        passed_gate_count=len(passed),
        failed_gate_count=len(failed),
        pending_gate_count=len(pending),
        graduation_eligible=(
            decision
            == CommitteeDecision.PROMOTE
        ),
        strengths=strengths,
        concerns=concerns,
        rationale=_decision_rationale(
            decision=decision,
            failed_count=len(failed),
            pending_count=len(pending),
        ),
        live_capital_authorized=False,
        human_approval_required=True,
    )


def get_capital_committee() -> dict[str, Any]:
    performance = get_performance_lab()

    reports = [
        evaluate_strategy_committee(
            strategy_performance=strategy,
        )
        for strategy
        in performance["strategies"]
    ]

    return {
        "status": "success",
        "generated_at": utc_now_iso(),
        "committee_mode": (
            "evidence_bound_advisory"
        ),
        "live_capital_enabled": False,
        "human_approval_required": True,
        "report_count": len(reports),
        "reports": [
            report.to_dict()
            for report in reports
        ],
    }
