from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class ScreeningRecommendation(str, Enum):
    RESEARCH = "research"
    WATCH = "watch"
    REJECT = "reject"


@dataclass(frozen=True)
class ScreeningScore:
    financial_quality: float
    automation_potential: float
    owner_independence: float
    operational_simplicity: float
    strategic_fit: float
    risk_quality: float

    overall_score: float
    recommendation: ScreeningRecommendation

    rationale: tuple[str, ...]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["recommendation"] = self.recommendation.value
        return data
