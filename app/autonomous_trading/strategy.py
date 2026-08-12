from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class StrategyAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass(frozen=True)
class StrategyCandidate:
    """
    A strategy-level trading candidate.

    This object represents what a strategy thinks should happen.
    It is not a trade proposal, risk approval, or execution request.
    """

    symbol: str
    action: StrategyAction

    confidence_percent: Decimal
    rationale: str

    suggested_position_percent: Decimal

    strategy_name: str


def create_strategy_candidate(
    *,
    symbol: str,
    action: StrategyAction,
    confidence_percent: Decimal,
    rationale: str,
    suggested_position_percent: Decimal,
    strategy_name: str,
) -> StrategyCandidate:
    normalized_symbol = symbol.strip().upper()
    normalized_rationale = rationale.strip()
    normalized_strategy_name = strategy_name.strip()

    if not normalized_symbol:
        raise ValueError("symbol must not be empty.")

    if confidence_percent < Decimal("0"):
        raise ValueError(
            "confidence_percent cannot be negative."
        )

    if confidence_percent > Decimal("100"):
        raise ValueError(
            "confidence_percent cannot exceed 100."
        )

    if suggested_position_percent < Decimal("0"):
        raise ValueError(
            "suggested_position_percent cannot be negative."
        )

    if suggested_position_percent > Decimal("100"):
        raise ValueError(
            "suggested_position_percent cannot exceed 100."
        )

    if not normalized_rationale:
        raise ValueError(
            "rationale must not be empty."
        )

    if not normalized_strategy_name:
        raise ValueError(
            "strategy_name must not be empty."
        )

    return StrategyCandidate(
        symbol=normalized_symbol,
        action=action,
        confidence_percent=confidence_percent,
        rationale=normalized_rationale,
        suggested_position_percent=(
            suggested_position_percent
        ),
        strategy_name=normalized_strategy_name,
    )


@dataclass(frozen=True)
class PositionContext:
    """
    Current portfolio position for a strategy-evaluated asset.

    A quantity of zero represents no open position.
    """

    symbol: str
    quantity: Decimal
    average_cost_usd: Decimal
    market_value_usd: Decimal
    allocation_percent: Decimal
    unrealized_gain_loss_usd: Decimal
    unrealized_gain_loss_percent: Decimal

    @property
    def has_position(self) -> bool:
        return self.quantity > Decimal("0")
