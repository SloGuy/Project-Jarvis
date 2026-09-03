from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class AllocationPolicy:
    name: str
    notional_capital_usd: Decimal
    minimum_cash_reserve_percent: Decimal
    maximum_total_allocation_percent: Decimal
    maximum_strategy_allocation_percent: Decimal
    insufficient_evidence_cap_percent: Decimal
    developing_evidence_cap_percent: Decimal
    substantial_evidence_cap_percent: Decimal
    minimum_regime_trade_count: int
    shadow_mode: bool
    paper_execution_enabled: bool
    live_execution_enabled: bool


CAPITAL_V2_SHADOW_POLICY = AllocationPolicy(
    name="capital_v2_shadow_v1",
    notional_capital_usd=Decimal("1000.00"),
    minimum_cash_reserve_percent=Decimal("40.00"),
    maximum_total_allocation_percent=Decimal("60.00"),
    maximum_strategy_allocation_percent=Decimal("50.00"),
    insufficient_evidence_cap_percent=Decimal("15.00"),
    developing_evidence_cap_percent=Decimal("35.00"),
    substantial_evidence_cap_percent=Decimal("50.00"),
    minimum_regime_trade_count=10,
    shadow_mode=True,
    paper_execution_enabled=False,
    live_execution_enabled=False,
)
