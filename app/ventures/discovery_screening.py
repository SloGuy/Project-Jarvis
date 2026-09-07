from __future__ import annotations

from math import isfinite

from app.ventures.automation_score import calculate_automation_opportunity
from app.ventures.models import VenturesOpportunity
from app.ventures.screening import screen_opportunity
from app.ventures.thesis import get_active_thesis


POLICY_VERSION = "discovery_screening_v1"


def evaluate_discovered_opportunity(
    opportunity: VenturesOpportunity,
) -> dict:
    thesis = get_active_thesis()
    missing = []
    invalid = []

    for field in (
        "asking_price_usd",
        "annual_revenue_usd",
        "annual_sde_usd",
        "owner_hours_per_week",
    ):
        value = getattr(opportunity, field)
        if value is None:
            missing.append(field)
            continue

        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
        ):
            invalid.append(field)
        elif field != "annual_sde_usd" and value < 0:
            invalid.append(field)

    preferred = (
        opportunity.business_type.value
        in thesis.preferred_business_types
    )
    reasons = []
    screening = None
    automation = None

    if invalid:
        disposition = "needs_data_review"
        reasons.append("Invalid numeric inputs require correction.")
    elif "asking_price_usd" in missing or opportunity.asking_price_usd <= 0:
        disposition = "needs_data_review"
        reasons.append("A positive asking price is required.")
    else:
        screening = screen_opportunity(opportunity).to_dict()
        automation = calculate_automation_opportunity(
            opportunity
        ).to_dict()

        if not preferred:
            disposition = "needs_classification_review"
            reasons.append(
                "The provisional business type does not establish "
                "a match with the acquisition thesis."
            )
        elif (
            opportunity.annual_sde_usd is not None
            and opportunity.annual_sde_usd <= 0
        ):
            disposition = "deprioritized"
            reasons.append(
                "Reported SDE does not establish positive cash flow."
            )
        elif (
            opportunity.annual_revenue_usd is not None
            and opportunity.annual_revenue_usd <= 0
        ):
            disposition = "deprioritized"
            reasons.append(
                "Reported annual revenue is not positive."
            )
        elif (
            opportunity.annual_sde_usd is not None
            and opportunity.asking_price_usd
            / opportunity.annual_sde_usd > 8
        ):
            disposition = "deprioritized"
            reasons.append("Asking price exceeds 8x reported annual SDE.")
        elif missing:
            disposition = "research_candidate"
            reasons.append(
                "Business type matches the thesis; missing inputs "
                "require research rather than automatic rejection."
            )
        elif screening["recommendation"] == "research":
            disposition = "research_candidate"
            reasons.append("Existing screening recommends research.")
        elif screening["recommendation"] == "watch":
            disposition = "watch"
            reasons.append("Existing screening recommends watching.")
        else:
            disposition = "deprioritized"
            reasons.append("Existing screening does not recommend research.")

    return {
        "opportunity_id": opportunity.opportunity_id,
        "policy_version": POLICY_VERSION,
        "thesis_id": thesis.thesis_id,
        "disposition": disposition,
        "research_candidate": disposition == "research_candidate",
        "missing_inputs": missing,
        "invalid_inputs": invalid,
        "rationale": reasons,
        "existing_screening": screening,
        "automation_heuristic": automation,
        "limitations": [
            "Source claims remain unverified.",
            "Automation scores are preliminary heuristics, not "
            "demonstrated workload reductions or profit improvements.",
            "Existing screening scores can be depressed by missing inputs; "
            "the disposition explicitly accounts for those gaps.",
        ],
        "advisory_only": True,
        "automatic_purchase_authority": False,
        "capital_transfer_authority": False,
    }
