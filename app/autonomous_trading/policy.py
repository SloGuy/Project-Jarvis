from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RiskPolicy:
    """
    Defines the risk constraints applied to autonomous trade proposals.

    This model contains configuration only. It does not evaluate trades,
    modify portfolios, or execute orders.
    """

    name: str
    starting_capital_usd: Decimal

    max_position_percent: Decimal
    max_total_exposure_percent: Decimal
    minimum_cash_reserve_percent: Decimal

    max_open_positions: int

    max_price_age_seconds: int
    minimum_confidence_percent: Decimal

    require_rationale: bool = True
    require_confidence: bool = True

    autonomous_execution_enabled: bool = False


INITIAL_1000_POLICY = RiskPolicy(
    name="initial_1000",
    starting_capital_usd=Decimal("1000.00"),

    max_position_percent=Decimal("20.00"),
    max_total_exposure_percent=Decimal("60.00"),
    minimum_cash_reserve_percent=Decimal("40.00"),

    max_open_positions=4,

    max_price_age_seconds=120,
    minimum_confidence_percent=Decimal("70.00"),

    require_rationale=True,
    require_confidence=True,

    autonomous_execution_enabled=False,
)
