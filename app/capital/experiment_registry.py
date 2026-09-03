from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from app.autonomous_trading.momentum_strategy import (
    STRATEGY_NAME as MOMENTUM_ALIGNMENT_V1,
)
from app.autonomous_trading.policy import (
    INITIAL_1000_POLICY,
)
from app.autonomous_trading.mean_reversion_strategy import (
    STRATEGY_NAME as MEAN_REVERSION_V1,
)
from app.capital.policies import (
    MEAN_REVERSION_1000_POLICY,
    VOLATILITY_BREAKOUT_1000_POLICY,
)
from app.capital.portfolio_service import (
    MEAN_REVERSION_PORTFOLIO_NAME,
    VOLATILITY_BREAKOUT_PORTFOLIO_NAME,
)
from app.autonomous_trading.volatility_breakout_strategy import (
    STRATEGY_NAME as VOLATILITY_BREAKOUT_V1,
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
    portfolio_name: str
    status: ExperimentStatus
    execution_mode: str
    started_at: datetime | None
    duration_days: int
    starting_capital_usd: Decimal
    risk_policy_name: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["started_at"] = (
            self.started_at.isoformat()
            if self.started_at is not None
            else None
        )
        data["starting_capital_usd"] = float(
            self.starting_capital_usd
        )
        return data


EXPERIMENT_DURATION_DAYS = 180

EXPERIMENT_STARTED_AT = datetime(
    2026,
    8,
    13,
    9,
    14,
    1,
    tzinfo=timezone.utc,
)

MOMENTUM_PAPER_EXPERIMENT_ID = (
    "momentum_alignment_v1_paper_2026"
)

MEAN_REVERSION_EXPERIMENT_ID = (
    "mean_reversion_v1_paper_2026"
)

VOLATILITY_BREAKOUT_EXPERIMENT_ID = (
    "volatility_breakout_v1_paper_2026"
)

MEAN_REVERSION_EXPERIMENT_STARTED_AT = datetime(
    2026,
    9,
    1,
    18,
    20,
    28,
    tzinfo=timezone.utc,
)


VOLATILITY_BREAKOUT_EXPERIMENT_STARTED_AT = datetime(
    2026,
    9,
    3,
    5,
    34,
    45,
    tzinfo=timezone.utc,
)


_EXPERIMENTS: dict[str, ExperimentDefinition] = {
    MOMENTUM_PAPER_EXPERIMENT_ID: ExperimentDefinition(
        experiment_id=MOMENTUM_PAPER_EXPERIMENT_ID,
        name="Momentum Alignment V1 Paper Experiment",
        strategy_name=MOMENTUM_ALIGNMENT_V1,
        portfolio_name="Primary Portfolio",
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
    MEAN_REVERSION_EXPERIMENT_ID: ExperimentDefinition(
        experiment_id=MEAN_REVERSION_EXPERIMENT_ID,
        name="Mean Reversion V1 Paper Experiment",
        strategy_name=MEAN_REVERSION_V1,
        portfolio_name=MEAN_REVERSION_PORTFOLIO_NAME,
        status=ExperimentStatus.RUNNING,
        execution_mode="autonomous_paper_trading",
        started_at=MEAN_REVERSION_EXPERIMENT_STARTED_AT,
        duration_days=180,
        starting_capital_usd=(
            MEAN_REVERSION_1000_POLICY.starting_capital_usd
        ),
        risk_policy_name=(
            MEAN_REVERSION_1000_POLICY.name
        ),
    ),
    VOLATILITY_BREAKOUT_EXPERIMENT_ID: (
        ExperimentDefinition(
            experiment_id=(
                VOLATILITY_BREAKOUT_EXPERIMENT_ID
            ),
            name=(
                "Volatility Breakout V1 "
                "Paper Experiment"
            ),
            strategy_name=(
                VOLATILITY_BREAKOUT_V1
            ),
            portfolio_name=(
                VOLATILITY_BREAKOUT_PORTFOLIO_NAME
            ),
            status=ExperimentStatus.RUNNING,
            execution_mode=(
                "autonomous_paper_trading"
                if VOLATILITY_BREAKOUT_1000_POLICY
                .autonomous_execution_enabled
                else "disabled"
            ),
            started_at=(
                VOLATILITY_BREAKOUT_EXPERIMENT_STARTED_AT
            ),
            duration_days=180,
            starting_capital_usd=(
                VOLATILITY_BREAKOUT_1000_POLICY
                .starting_capital_usd
            ),
            risk_policy_name=(
                VOLATILITY_BREAKOUT_1000_POLICY.name
            ),
        )
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
