from typing import Any

from app.autonomous_trading.experiment_status import (
    get_experiment_status,
)
from app.capital.experiment_registry import (
    ExperimentStatus,
    list_experiments,
)
from app.capital.strategy_registry import (
    list_strategies,
)
from app.capital.portfolio_status import (
    get_strategy_portfolios,
)
from app.capital.research_service import (
    get_research_summary,
)
from app.capital.committee_service import (
    get_capital_committee,
)
from app.capital.safety_audit import (
    get_capital_safety_audit,
)


def get_capital_status(
    *,
    portfolio_id: int | None = None,
    decision_limit: int = 10,
) -> dict[str, Any]:
    strategies = list_strategies()
    experiments = list_experiments()

    running_experiments = [
        experiment
        for experiment in experiments
        if experiment.status == ExperimentStatus.RUNNING
    ]

    active_experiment = get_experiment_status(
        portfolio_id=portfolio_id,
        decision_limit=decision_limit,
    )

    return {
        "status": "success",
        "generated_at": active_experiment["generated_at"],
        "organization": {
            "name": "Jarvis Capital",
            "operating_mode": (
                "research_and_paper_validation"
            ),
            "live_capital_enabled": False,
            "strategy_count": len(strategies),
            "experiment_count": len(experiments),
            "running_experiment_count": len(
                running_experiments
            ),
        },
        "strategies": [
            strategy.to_dict()
            for strategy in strategies
        ],
        "experiments": [
            experiment.to_dict()
            for experiment in experiments
        ],
        "strategy_portfolios": (
            get_strategy_portfolios()
        ),
        "research": (
            get_research_summary()
        ),
        "capital_committee": (
            get_capital_committee()
        ),
        "safety_audit": (
            get_capital_safety_audit()
        ),
        "active_experiment": active_experiment,
    }
