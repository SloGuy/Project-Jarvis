from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite


@dataclass(frozen=True)
class ImprovementEconomics:
    implementation_cost_usd: float
    monthly_operating_cost_usd: float
    hours_saved_per_month: float
    labor_value_per_hour_usd: float
    monthly_incremental_gross_profit_usd: float
    assumptions_notes: str

    def __post_init__(self):
        values = (
            self.implementation_cost_usd,
            self.monthly_operating_cost_usd,
            self.hours_saved_per_month,
            self.labor_value_per_hour_usd,
            self.monthly_incremental_gross_profit_usd,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or value < 0
            for value in values
        ):
            raise ValueError("Economic assumptions must be finite and nonnegative.")
        if not self.assumptions_notes.strip():
            raise ValueError("Economic assumption notes are required.")

    def to_dict(self) -> dict:
        labor_value = (
            self.hours_saved_per_month * self.labor_value_per_hour_usd
        )
        cash_effect = (
            self.monthly_incremental_gross_profit_usd
            - self.monthly_operating_cost_usd
        )
        combined_value = cash_effect + labor_value
        if not all(isfinite(value) for value in (
            labor_value, cash_effect, combined_value
        )):
            raise ValueError("Economic calculation exceeded numeric limits.")

        return {
            **asdict(self),
            "monthly_owner_time_value_usd": round(labor_value, 2),
            "monthly_cash_effect_before_implementation_usd": round(
                cash_effect, 2
            ),
            "monthly_combined_economic_value_usd": round(combined_value, 2),
            "basis": "scenario_assumptions_not_verified_savings",
            "limitations": [
                "Owner time value is not automatically cash savings.",
                "Incremental gross profit must exclude benefits already "
                "counted as saved labor.",
                "Implementation cost is a separate upfront expense.",
                "Proposals may overlap; benefits must not be summed "
                "without checking for double counting.",
            ],
        }


@dataclass(frozen=True)
class ImprovementProposal:
    proposal_id: str
    opportunity_id: str
    research_created_at: str
    title: str
    source_claim_ids: tuple[str, ...]
    current_task: str
    proposed_workflow: tuple[str, ...]
    implementation_requirements: tuple[str, ...]
    validation_steps: tuple[str, ...]
    human_oversight: str
    economics: ImprovementEconomics | None = None

    def __post_init__(self):
        for value in (
            self.proposal_id,
            self.opportunity_id,
            self.research_created_at,
            self.title,
            self.current_task,
            self.human_oversight,
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Proposal text fields must be nonempty.")

        for values in (
            self.source_claim_ids,
            self.proposed_workflow,
            self.implementation_requirements,
            self.validation_steps,
        ):
            if not values or any(
                not isinstance(value, str) or not value.strip()
                for value in values
            ):
                raise ValueError("Proposal lists must contain nonempty text.")

        if len(set(self.source_claim_ids)) != len(self.source_claim_ids):
            raise ValueError("Duplicate source claim references.")

    def to_dict(self) -> dict:
        data = asdict(self)
        data["economics"] = (
            self.economics.to_dict() if self.economics else None
        )
        data.update({
            "status": "proposed",
            "basis": "automation_hypothesis",
            "implementation_authority": False,
            "automatic_purchase_authority": False,
            "capital_transfer_authority": False,
        })
        return data
