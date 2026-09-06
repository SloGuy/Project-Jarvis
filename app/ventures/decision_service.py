from __future__ import annotations

import uuid

from app.ventures.decision_models import (
    ReviewDecision,
    VenturesDecision,
)
from app.ventures.decision_store import save_decision
from app.ventures.models import OpportunityStatus, utc_now
from app.ventures.opportunities import get_opportunity
from app.ventures.research_store import get_latest_research_report
from app.ventures.underwriting_store import list_underwriting_reports


class DecisionConflict(ValueError):
    pass


def record_review_decision(
    *,
    opportunity_id: str,
    decision: ReviewDecision,
    recorded_by: str,
    rationale: str,
) -> dict:
    if not recorded_by.strip():
        raise ValueError("A reviewer name is required.")
    if not rationale.strip():
        raise ValueError("Decision rationale is required.")

    opportunity = get_opportunity(opportunity_id)
    if opportunity is None:
        raise KeyError("Ventures opportunity not found.")

    research = get_latest_research_report(opportunity_id)
    underwriting_history = list_underwriting_reports(opportunity_id)
    underwriting = (
        underwriting_history[-1] if underwriting_history else None
    )

    if decision == ReviewDecision.APPROVE:
        if opportunity.status != OpportunityStatus.COMMITTEE:
            raise DecisionConflict(
                "Approval requires committee stage."
            )

        if research is None:
            raise DecisionConflict(
                "Approval requires a research report."
            )

        if (
            research["report"]["recommendation"]
            != "ready_for_underwriting"
        ):
            raise DecisionConflict(
                "Research has not recommended readiness for underwriting."
            )

        if underwriting is None:
            raise DecisionConflict(
                "Approval requires an underwriting report."
            )

        report = underwriting["report"]
        if (
            report.get("research_created_at") != research["created_at"]
            or report.get("research_recommendation")
            != "ready_for_underwriting"
        ):
            raise DecisionConflict(
                "Underwriting must reference the latest ready research."
            )

        if (
            report.get("opportunity_id") != opportunity_id
            or research["report"].get("opportunity_id") != opportunity_id
        ):
            raise DecisionConflict(
                "Reports must belong to this opportunity."
            )

        scenarios = report.get("scenarios", [])
        if (
            report.get("missing_inputs") != []
            or len(scenarios) != 2
            or {item.get("name") for item in scenarios}
            != {"base", "downside"}
        ):
            raise DecisionConflict(
                "Approval requires complete base and downside underwriting."
            )

    record = VenturesDecision(
        decision_id=f"decision_{uuid.uuid4().hex[:12]}",
        opportunity_id=opportunity_id,
        created_at=utc_now().isoformat(),
        decision=decision,
        recorded_by=recorded_by.strip(),
        rationale=rationale.strip(),
        opportunity_status_at_review=opportunity.status.value,
        research_created_at=(
            research["created_at"] if research is not None else None
        ),
        underwriting_created_at=(
            underwriting["created_at"]
            if underwriting is not None else None
        ),
    )
    return save_decision(record)
