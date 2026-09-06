from __future__ import annotations

from app.ventures.research_models import (
    ResearchRecommendation,
    VenturesResearchReport,
)


def evaluate_research_gate(
    report: VenturesResearchReport,
) -> ResearchRecommendation:
    high_risks = [
        risk
        for risk in report.risks
        if risk.severity == "high"
    ]

    contradicted_claims = [
        claim
        for claim in report.claims
        if claim.evidence_status.value == "contradicted"
    ]

    if contradicted_claims:
        return ResearchRecommendation.REJECT

    if high_risks:
        return ResearchRecommendation.MORE_RESEARCH_REQUIRED

    if report.evidence_score >= 75.0:
        return ResearchRecommendation.READY_FOR_UNDERWRITING

    return ResearchRecommendation.MORE_RESEARCH_REQUIRED
