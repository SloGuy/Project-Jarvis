from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite


@dataclass(frozen=True)
class UnderwritingAssumptions:
    acquisition_costs_usd: float
    working_capital_usd: float
    annual_added_operating_costs_usd: float
    owner_hourly_cost_usd: float
    automation_hours_saved_per_week: float
    downside_revenue_decline_percent: float
    notes: str

    def __post_init__(self):
        numeric_fields = (
            self.acquisition_costs_usd,
            self.working_capital_usd,
            self.annual_added_operating_costs_usd,
            self.owner_hourly_cost_usd,
            self.automation_hours_saved_per_week,
            self.downside_revenue_decline_percent,
        )
        if any(
            not isfinite(value) or value < 0
            for value in numeric_fields
        ):
            raise ValueError(
                "Assumptions must be finite and nonnegative."
            )
        if self.downside_revenue_decline_percent > 100:
            raise ValueError(
                "Revenue decline cannot exceed 100 percent."
            )
        if not self.notes.strip():
            raise ValueError("Assumption notes are required.")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class UnderwritingScenario:
    name: str
    annual_revenue_usd: float
    annual_sde_usd: float
    remaining_owner_hours_per_week: float
    annual_owner_labor_cost_usd: float
    annual_adjusted_cash_flow_usd: float
    cash_return_percent: float | None
    payback_years: float | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class VenturesUnderwritingReport:
    opportunity_id: str
    research_created_at: str | None
    research_recommendation: str | None
    assumptions: UnderwritingAssumptions
    total_investment_usd: float
    asking_price_to_sde_multiple: float | None
    scenarios: tuple[UnderwritingScenario, ...]
    missing_inputs: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "opportunity_id": self.opportunity_id,
            "research_created_at": self.research_created_at,
            "research_recommendation": self.research_recommendation,
            "assumptions": self.assumptions.to_dict(),
            "total_investment_usd": self.total_investment_usd,
            "asking_price_to_sde_multiple": (
                self.asking_price_to_sde_multiple
            ),
            "scenarios": [
                scenario.to_dict()
                for scenario in self.scenarios
            ],
            "missing_inputs": list(self.missing_inputs),
            "limitations": list(self.limitations),
            "advisory_only": True,
            "human_approval_required": True,
            "acquisition_authority": False,
        }
