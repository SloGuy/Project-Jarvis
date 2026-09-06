from __future__ import annotations

from dataclasses import replace

from app.ventures.research_gate import evaluate_research_gate

from app.ventures.evidence import (
    apply_evidence_to_claim,
    calculate_evidence_score,
)
from app.ventures.research_models import (
    EvidenceQuality,
    EvidenceStatus,
    ResearchClaim,
    ResearchRecommendation,
    VenturesResearchReport,
    DiligenceQuestion,
    ResearchRisk,
)
from app.ventures.research_store import (
    get_latest_research_report,
    save_research_report,
)


def _claim_from_dict(
    data: dict,
) -> ResearchClaim:
    return ResearchClaim(
        claim_id=data["claim_id"],
        category=data["category"],
        claim=data["claim"],
        source=data["source"],
        evidence_status=EvidenceStatus(
            data["evidence_status"]
        ),
        evidence_quality=EvidenceQuality(
            data["evidence_quality"]
        ),
        evidence_notes=data.get(
            "evidence_notes"
        ),
    )


def _risk_from_dict(
    data: dict,
) -> ResearchRisk:
    return ResearchRisk(
        category=data["category"],
        description=data["description"],
        severity=data["severity"],
    )


def _question_from_dict(
    data: dict,
) -> DiligenceQuestion:
    return DiligenceQuestion(
        category=data["category"],
        question=data["question"],
        priority=data["priority"],
    )


def update_claim_evidence(
    *,
    opportunity_id: str,
    claim_id: str,
    evidence_status: EvidenceStatus,
    evidence_quality: EvidenceQuality,
    evidence_notes: str,
) -> dict:
    latest = get_latest_research_report(
        opportunity_id
    )

    if latest is None:
        raise ValueError(
            "No research report exists for opportunity."
        )

    report_data = latest["report"]

    claims = tuple(
        _claim_from_dict(data)
        for data in report_data["claims"]
    )

    updated_claims = []
    found = False

    for claim in claims:
        if claim.claim_id == claim_id:
            claim = apply_evidence_to_claim(
                claim,
                evidence_status=evidence_status,
                evidence_quality=evidence_quality,
                evidence_notes=evidence_notes,
            )
            found = True

        updated_claims.append(claim)

    if not found:
        raise ValueError(
            f"Research claim not found: {claim_id}"
        )

    evidence_score = calculate_evidence_score(
        tuple(updated_claims)
    )

    risks = tuple(
        _risk_from_dict(data)
        for data in report_data["risks"]
    )

    diligence_questions = tuple(
        _question_from_dict(data)
        for data in report_data["diligence_questions"]
    )

    updated_report = VenturesResearchReport(
        opportunity_id=opportunity_id,
        claims=tuple(updated_claims),
        risks=risks,
        diligence_questions=diligence_questions,
        evidence_score=evidence_score,
        recommendation=(
            ResearchRecommendation(
                report_data["recommendation"]
            )
        ),
        summary=report_data["summary"],
    )

    updated_report = replace(
        updated_report,
        recommendation=evaluate_research_gate(updated_report),
    )

    return save_research_report(updated_report)
