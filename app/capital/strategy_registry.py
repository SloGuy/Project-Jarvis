from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from app.autonomous_trading.momentum_strategy import (
    STRATEGY_NAME as MOMENTUM_ALIGNMENT_V1,
)
from app.autonomous_trading.mean_reversion_strategy import (
    STRATEGY_NAME as MEAN_REVERSION_V1,
)
from app.autonomous_trading.volatility_breakout_strategy import (
    STRATEGY_NAME as VOLATILITY_BREAKOUT_V1,
)


class StrategyStage(str, Enum):
    CANDIDATE = "candidate"
    BACKTEST = "backtest"
    PAPER = "paper"
    VALIDATED = "validated"
    LIVE_CANDIDATE = "live_candidate"
    LIVE = "live"
    RETIRED = "retired"


@dataclass(frozen=True)
class StrategyDefinition:
    name: str
    display_name: str
    description: str
    version: str
    stage: StrategyStage
    implementation_module: str
    evaluator_name: str
    active_experiment: bool
    enabled: bool

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stage"] = self.stage.value
        return data


_STRATEGIES: dict[str, StrategyDefinition] = {
    MOMENTUM_ALIGNMENT_V1: StrategyDefinition(
        name=MOMENTUM_ALIGNMENT_V1,
        display_name="Momentum Alignment V1",
        description=(
            "Position-aware momentum strategy using aligned "
            "15-minute and 24-hour market movement."
        ),
        version="1.0",
        stage=StrategyStage.PAPER,
        implementation_module=(
            "app.autonomous_trading.momentum_strategy"
        ),
        evaluator_name="evaluate_momentum_strategy",
        active_experiment=True,
        enabled=True,
    ),
    MEAN_REVERSION_V1: StrategyDefinition(
        name=MEAN_REVERSION_V1,
        display_name="Mean Reversion V1",
        description=(
            "Long-only statistical mean-reversion strategy "
            "using rolling price deviation and recovery."
        ),
        version="1.0",
        stage=StrategyStage.PAPER,
        implementation_module=(
            "app.autonomous_trading.mean_reversion_strategy"
        ),
        evaluator_name="evaluate_mean_reversion_strategy",
        active_experiment=True,
        enabled=True,
    ),
    VOLATILITY_BREAKOUT_V1: StrategyDefinition(
        name=VOLATILITY_BREAKOUT_V1,
        display_name="Volatility Breakout V1",
        description=(
            "Long-only volatility breakout strategy "
            "using range compression, price expansion, "
            "directional confirmation, and bounded exits."
        ),
        version="1.0",
        stage=StrategyStage.PAPER,
        implementation_module=(
            "app.autonomous_trading."
            "volatility_breakout_strategy"
        ),
        evaluator_name=(
            "evaluate_volatility_breakout_strategy"
        ),
        active_experiment=True,
        enabled=True,
    ),
}


def list_strategies() -> list[StrategyDefinition]:
    return list(_STRATEGIES.values())


def get_strategy(
    *,
    strategy_name: str,
) -> StrategyDefinition | None:
    normalized_name = strategy_name.strip()

    if not normalized_name:
        raise ValueError("strategy_name must not be empty.")

    return _STRATEGIES.get(normalized_name)


def require_strategy(
    *,
    strategy_name: str,
) -> StrategyDefinition:
    strategy = get_strategy(
        strategy_name=strategy_name,
    )

    if strategy is None:
        raise KeyError(
            f"Unknown Jarvis Capital strategy: {strategy_name}"
        )

    return strategy
