from __future__ import annotations

from dataclasses import replace

from app.ventures.research_models import (
    EvidenceQuality,
    EvidenceStatus,
    ResearchClaim,
)


def apply_evidence_to_claim(
    claim: ResearchClaim,
    *,
    evidence_status: EvidenceStatus,
    evidence_quality: EvidenceQuality,
    evidence_notes: str,
) -> ResearchClaim:
    return replace(
        claim,
        evidence_status=evidence_status,
        evidence_quality=evidence_quality,
        evidence_notes=evidence_notes,
    )


def calculate_evidence_score(
    claims: tuple[ResearchClaim, ...],
) -> float:
    if not claims:
        return 0.0

    status_scores = {
        EvidenceStatus.UNVERIFIED: 0.0,
        EvidenceStatus.PARTIALLY_SUPPORTED: 50.0,
        EvidenceStatus.SUPPORTED: 100.0,
        EvidenceStatus.CONTRADICTED: 0.0,
    }

    quality_weights = {
        EvidenceQuality.LOW: 0.50,
        EvidenceQuality.MEDIUM: 0.75,
        EvidenceQuality.HIGH: 1.00,
    }

    total = 0.0

    for claim in claims:
        total += (
            status_scores[claim.evidence_status]
            * quality_weights[claim.evidence_quality]
        )

    return round(
        total / len(claims),
        2,
    )
