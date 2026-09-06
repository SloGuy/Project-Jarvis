from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Tuple


@dataclass(frozen=True)
class VenturesAcquisitionThesis:
    thesis_id: str
    name: str
    description: str

    preferred_business_types: Tuple[str, ...]
    preferred_characteristics: Tuple[str, ...]
    avoid_characteristics: Tuple[str, ...]

    automatic_offer_authority: bool
    automatic_purchase_authority: bool
    capital_transfer_authority: bool
    human_acquisition_approval_required: bool

    def to_dict(self) -> dict:
        return asdict(self)


VENTURES_V1_THESIS = VenturesAcquisitionThesis(
    thesis_id="ventures_v1_digital_cashflow",
    name="Jarvis Ventures V1 Digital Cash Flow Thesis",
    description=(
        "Acquire small, understandable, cash-flowing digital businesses "
        "where software, automation, and AI can materially reduce owner "
        "workload while preserving or improving business performance."
    ),
    preferred_business_types=(
        "saas",
        "micro_saas",
        "subscription_data",
        "lead_generation",
        "digital_content",
        "productized_service",
    ),
    preferred_characteristics=(
        "positive_cash_flow",
        "recurring_or_repeat_revenue",
        "low_operational_complexity",
        "measurable_customer_economics",
        "low_owner_dependency",
        "high_automation_potential",
        "stable_or_growing_demand",
        "understandable_business_model",
    ),
    avoid_characteristics=(
        "undocumented_financials",
        "extreme_customer_concentration",
        "owner_is_primary_product",
        "high_physical_labor_dependency",
        "unverifiable_revenue",
        "material_legal_uncertainty",
        "business_model_dependency_on_single_platform",
        "unbounded_operational_complexity",
    ),
    automatic_offer_authority=False,
    automatic_purchase_authority=False,
    capital_transfer_authority=False,
    human_acquisition_approval_required=True,
)


def get_active_thesis() -> VenturesAcquisitionThesis:
    return VENTURES_V1_THESIS
