from __future__ import annotations

from dataclasses import asdict, dataclass

from app.ventures.models import VenturesOpportunity


@dataclass(frozen=True)
class AutomationOpportunityScore:
    score: float
    workload_reduction_potential: float
    digital_operability: float
    owner_workload_leverage: float
    rationale: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def calculate_automation_opportunity(
    opportunity: VenturesOpportunity,
) -> AutomationOpportunityScore:
    business_type = opportunity.business_type.value
    owner_hours = opportunity.owner_hours_per_week

    if business_type in {
        "micro_saas",
        "saas",
        "subscription_data",
    }:
        digital_operability = 95.0
    elif business_type in {
        "lead_generation",
        "digital_content",
    }:
        digital_operability = 90.0
    elif business_type == "productized_service":
        digital_operability = 70.0
    else:
        digital_operability = 40.0

    if owner_hours is None:
        owner_workload_leverage = 50.0
    elif owner_hours >= 40:
        owner_workload_leverage = 65.0
    elif owner_hours >= 25:
        owner_workload_leverage = 80.0
    elif owner_hours >= 10:
        owner_workload_leverage = 95.0
    elif owner_hours >= 5:
        owner_workload_leverage = 85.0
    else:
        owner_workload_leverage = 60.0

    workload_reduction_potential = (
        digital_operability * 0.60
        + owner_workload_leverage * 0.40
    )

    score = _clamp(
        digital_operability * 0.50
        + owner_workload_leverage * 0.30
        + workload_reduction_potential * 0.20
    )

    rationale: list[str] = []

    if digital_operability >= 90:
        rationale.append(
            "Business model is highly compatible with digital automation."
        )

    if owner_workload_leverage >= 85:
        rationale.append(
            "Existing owner workload provides meaningful automation leverage."
        )

    if workload_reduction_potential >= 85:
        rationale.append(
            "Substantial owner workload reduction appears achievable."
        )

    return AutomationOpportunityScore(
        score=round(score, 2),
        workload_reduction_potential=round(
            workload_reduction_potential,
            2,
        ),
        digital_operability=round(
            digital_operability,
            2,
        ),
        owner_workload_leverage=round(
            owner_workload_leverage,
            2,
        ),
        rationale=tuple(rationale),
    )
