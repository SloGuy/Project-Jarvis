from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class CommitteeDecision(str, Enum):
    CONTINUE = "continue"
    REVISE = "revise"
    KILL = "kill"
    PROMOTE = "promote"


class GateStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    PENDING = "pending"


@dataclass(frozen=True)
class GraduationGate:
    gate_name: str
    status: GateStatus
    actual_value: Any
    required_value: Any
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class CommitteeReport:
    strategy_name: str
    experiment_id: str
    portfolio_id: int
    decision: CommitteeDecision
    generated_at: str

    gate_results: tuple[
        GraduationGate,
        ...
    ]

    passed_gate_count: int
    failed_gate_count: int
    pending_gate_count: int
    graduation_eligible: bool

    strengths: tuple[str, ...]
    concerns: tuple[str, ...]
    rationale: str

    live_capital_authorized: bool = False
    human_approval_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["decision"] = (
            self.decision.value
        )
        data["gate_results"] = [
            gate.to_dict()
            for gate in self.gate_results
        ]
        return data
