from app.ventures.opportunities import list_opportunities
from app.ventures.research_models import (
    DiligenceQuestion,
    EvidenceQuality,
    EvidenceStatus,
    ResearchClaim,
    ResearchRecommendation,
    VenturesResearchReport,
)
from app.ventures.research_gate import (
    evaluate_research_gate,
)


opportunity = next(
    item
    for item in list_opportunities()
    if item.name == "TinyCRM"
)

claims = tuple(
    ResearchClaim(
        claim_id=f"gate_{index}",
        category="test",
        claim="Verified test claim.",
        source="test",
        evidence_status=EvidenceStatus.SUPPORTED,
        evidence_quality=EvidenceQuality.HIGH,
        evidence_notes="Gate test.",
    )
    for index in range(4)
)

report = VenturesResearchReport(
    opportunity_id=opportunity.opportunity_id,
    claims=claims,
    risks=tuple(),
    diligence_questions=tuple(),
    evidence_score=100.0,
    recommendation=ResearchRecommendation.MORE_RESEARCH_REQUIRED,
    summary="Gate test.",
)

result = evaluate_research_gate(report)

assert result == ResearchRecommendation.READY_FOR_UNDERWRITING

print("ventures_research_gate: PASS")
print("recommendation:", result.value)
