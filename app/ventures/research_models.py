from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class EvidenceStatus(str, Enum):
    UNVERIFIED = "unverified"
    PARTIALLY_SUPPORTED = "partially_supported"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"


class EvidenceQuality(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ResearchRecommendation(str, Enum):
    READY_FOR_UNDERWRITING = "ready_for_underwriting"
    MORE_RESEARCH_REQUIRED = "more_research_required"
    REJECT = "reject"


@dataclass(frozen=True)
class ResearchClaim:
    claim_id: str
    category: str
    claim: str
    source: str
    evidence_status: EvidenceStatus
    evidence_quality: EvidenceQuality
    evidence_notes: str | None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["evidence_status"] = self.evidence_status.value
        data["evidence_quality"] = self.evidence_quality.value
        return data


@dataclass(frozen=True)
class ResearchRisk:
    category: str
    description: str
    severity: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DiligenceQuestion:
    category: str
    question: str
    priority: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class VenturesResearchReport:
    opportunity_id: str

    claims: tuple[ResearchClaim, ...]
    risks: tuple[ResearchRisk, ...]
    diligence_questions: tuple[DiligenceQuestion, ...]

    evidence_score: float
    recommendation: ResearchRecommendation

    summary: str

    def to_dict(self) -> dict:
        return {
            "opportunity_id": self.opportunity_id,
            "claims": [
                claim.to_dict()
                for claim in self.claims
            ],
            "risks": [
                risk.to_dict()
                for risk in self.risks
            ],
            "diligence_questions": [
                question.to_dict()
                for question in self.diligence_questions
            ],
            "evidence_score": self.evidence_score,
            "recommendation": self.recommendation.value,
            "summary": self.summary,
        }
