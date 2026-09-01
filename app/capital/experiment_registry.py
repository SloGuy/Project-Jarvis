from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from app.autonomous_trading.experiment_status import (
    EXPERIMENT_DURATION_DAYS,
    EXPERIMENT_STARTED_AT,
)
from app.autonomous_trading.momentum_strategy import (
    STRATEGY_NAME as MOMENTUM_ALIGNMENT_V1,
)
from app.autonomous_trading.policy import (
    INITIAL_1000_POLICY,
)


class ExperimentStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ExperimentDefinition:
    experiment_id: str
    name: str
    strategy_name: str
    status: ExperimentStatus
    execution_mode: str
    started_at: datetime
    duration_days: int
    starting_capital_usd: Decimal
    risk_policy_name: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["started_at"] = self.started_at.isoformat()
        data["starting_capital_usd"] = float(
            self.starting_capital_usd
        )
        return data


MOMENTUM_PAPER_EXPERIMENT_ID = (
    "momentum_alignment_v1_paper_2026"
)


_EXPERIMENTS: dict[str, ExperimentDefinition] = {
    MOMENTUM_PAPER_EXPERIMENT_ID: ExperimentDefinition(
        experiment_id=MOMENTUM_PAPER_EXPERIMENT_ID,
        name="Momentum Alignment V1 Paper Experiment",
        strategy_name=MOMENTUM_ALIGNMENT_V1,
        status=ExperimentStatus.RUNNING,
        execution_mode=(
            "autonomous_paper_trading"
            if INITIAL_1000_POLICY.autonomous_execution_enabled
            else "disabled"
        ),
        started_at=EXPERIMENT_STARTED_AT,
        duration_days=EXPERIMENT_DURATION_DAYS,
        starting_capital_usd=(
            INITIAL_1000_POLICY.starting_capital_usd
        ),
        risk_policy_name=INITIAL_1000_POLICY.name,
    ),
}


def list_experiments() -> list[ExperimentDefinition]:
    return list(_EXPERIMENTS.values())


def get_experiment(
    *,
    experiment_id: str,
) -> ExperimentDefinition | None:
    normalized_id = experiment_id.strip()

    if not normalized_id:
        raise ValueError("experiment_id must not be empty.")

    return _EXPERIMENTS.get(normalized_id)


def require_experiment(
    *,
    experiment_id: str,
) -> ExperimentDefinition:
    experiment = get_experiment(
        experiment_id=experiment_id,
    )

    if experiment is None:
        raise KeyError(
            f"Unknown Jarvis Capital experiment: {experiment_id}"
        )

    return experiment
