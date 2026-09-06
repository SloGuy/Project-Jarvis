from app.ventures.opportunities import list_opportunities
from app.ventures.research_models import (
    EvidenceQuality,
    EvidenceStatus,
)
from app.ventures.research_store import (
    get_latest_research_report,
)
from app.ventures.research_updates import (
    update_claim_evidence,
)


opportunity = next(
    item
    for item in list_opportunities()
    if item.name == "TinyCRM"
)

latest = get_latest_research_report(
    opportunity.opportunity_id
)

claim = latest["report"]["claims"][1]

updated = update_claim_evidence(
    opportunity_id=opportunity.opportunity_id,
    claim_id=claim["claim_id"],
    evidence_status=EvidenceStatus.SUPPORTED,
    evidence_quality=EvidenceQuality.HIGH,
    evidence_notes="Regression test evidence.",
)

assert len(
    updated["report"]["diligence_questions"]
) == 6

assert len(
    updated["report"]["claims"]
) == 4

print("research_update_preservation: PASS")
print(
    "evidence_score:",
    updated["report"]["evidence_score"],
)
