from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ReviewDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    DEFER = "defer"
    MORE_RESEARCH = "more_research"


@dataclass(frozen=True)
class VenturesDecision:
    decision_id: str
    opportunity_id: str
    created_at: str
    decision: ReviewDecision
    recorded_by: str
    rationale: str
    opportunity_status_at_review: str
    research_created_at: str | None
    underwriting_created_at: str | None

    def __post_init__(self):
        if not self.recorded_by.strip():
            raise ValueError("A reviewer name is required.")
        if not self.rationale.strip():
            raise ValueError("Decision rationale is required.")

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "opportunity_id": self.opportunity_id,
            "created_at": self.created_at,
            "decision": self.decision.value,
            "recorded_by": self.recorded_by,
            "rationale": self.rationale,
            "opportunity_status_at_review": (
                self.opportunity_status_at_review
            ),
            "research_created_at": self.research_created_at,
            "underwriting_created_at": self.underwriting_created_at,
            "decision_scope": "acquisition_review_only",
            "reviewer_identity_verified": False,
            "acquisition_authority": False,
            "capital_deployment_authority": False,
        }
