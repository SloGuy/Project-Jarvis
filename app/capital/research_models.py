from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ResearchStatus(str, Enum):
    PROPOSED = "proposed"
    SCREENING = "screening"
    RESEARCHING = "researching"
    REVISION_REQUIRED = "revision_required"
    READY_FOR_EXPERIMENT = "ready_for_experiment"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class ResearchVerdict(str, Enum):
    PENDING = "pending"
    PROMISING = "promising"
    INCONCLUSIVE = "inconclusive"
    UNPROMISING = "unpromising"


@dataclass
class ResearchCandidate:
    research_id: str
    strategy_name: str
    display_name: str
    hypothesis: str
    description: str
    market_regime: str
    asset_universe: list[str]
    data_requirements: list[str]
    risk_thesis: str
    success_criteria: list[str]

    status: ResearchStatus
    verdict: ResearchVerdict

    proposed_by: str
    created_at: str
    updated_at: str

    evidence: list[str] = field(
        default_factory=list
    )
    concerns: list[str] = field(
        default_factory=list
    )
    evaluation_notes: str | None = None
    reviewed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["verdict"] = self.verdict.value
        return data

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> "ResearchCandidate":
        return cls(
            research_id=str(
                data["research_id"]
            ),
            strategy_name=str(
                data["strategy_name"]
            ),
            display_name=str(
                data["display_name"]
            ),
            hypothesis=str(
                data["hypothesis"]
            ),
            description=str(
                data["description"]
            ),
            market_regime=str(
                data["market_regime"]
            ),
            asset_universe=[
                str(value)
                for value in data.get(
                    "asset_universe",
                    [],
                )
            ],
            data_requirements=[
                str(value)
                for value in data.get(
                    "data_requirements",
                    [],
                )
            ],
            risk_thesis=str(
                data["risk_thesis"]
            ),
            success_criteria=[
                str(value)
                for value in data.get(
                    "success_criteria",
                    [],
                )
            ],
            status=ResearchStatus(
                data["status"]
            ),
            verdict=ResearchVerdict(
                data.get(
                    "verdict",
                    ResearchVerdict.PENDING.value,
                )
            ),
            proposed_by=str(
                data["proposed_by"]
            ),
            created_at=str(
                data["created_at"]
            ),
            updated_at=str(
                data["updated_at"]
            ),
            evidence=[
                str(value)
                for value in data.get(
                    "evidence",
                    [],
                )
            ],
            concerns=[
                str(value)
                for value in data.get(
                    "concerns",
                    [],
                )
            ],
            evaluation_notes=data.get(
                "evaluation_notes"
            ),
            reviewed_at=data.get(
                "reviewed_at"
            ),
        )
