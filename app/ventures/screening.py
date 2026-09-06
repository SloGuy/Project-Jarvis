from __future__ import annotations
from app.ventures.automation_score import (
    calculate_automation_opportunity,
)

from app.ventures.models import VenturesOpportunity
from app.ventures.screening_models import (
    ScreeningRecommendation,
    ScreeningScore,
)
from app.ventures.thesis import get_active_thesis


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _score_financial_quality(
    opportunity: VenturesOpportunity,
) -> float:
    score = 20.0

    revenue = opportunity.annual_revenue_usd
    sde = opportunity.annual_sde_usd

    if revenue is None or revenue <= 0:
        return 10.0

    score += 15.0

    if sde is None:
        return _clamp(score)

    if sde <= 0:
        return 5.0

    score += 15.0

    margin = sde / revenue

    if margin >= 0.50:
        score += 50.0
    elif margin >= 0.40:
        score += 45.0
    elif margin >= 0.30:
        score += 35.0
    elif margin >= 0.20:
        score += 25.0
    elif margin >= 0.10:
        score += 10.0
    else:
        score -= 10.0

    return _clamp(score)


def _score_automation_potential(
    opportunity: VenturesOpportunity,
) -> float:
    return calculate_automation_opportunity(
        opportunity
    ).score


def _score_owner_independence(
    opportunity: VenturesOpportunity,
) -> float:
    hours = opportunity.owner_hours_per_week

    if hours is None:
        return 50.0

    if hours <= 5:
        return 95.0

    if hours <= 10:
        return 85.0

    if hours <= 20:
        return 70.0

    if hours <= 30:
        return 50.0

    if hours <= 40:
        return 30.0

    return 15.0


def _score_operational_simplicity(
    opportunity: VenturesOpportunity,
) -> float:
    if opportunity.business_type.value in {
        "micro_saas",
        "subscription_data",
        "digital_content",
    }:
        return 90.0

    if opportunity.business_type.value in {
        "saas",
        "lead_generation",
    }:
        return 80.0

    if opportunity.business_type.value == "productized_service":
        return 60.0

    return 40.0


def _score_strategic_fit(
    opportunity: VenturesOpportunity,
) -> float:
    thesis = get_active_thesis()

    if (
        opportunity.business_type.value
        in thesis.preferred_business_types
    ):
        return 90.0

    return 40.0


def _score_risk_quality(
    opportunity: VenturesOpportunity,
) -> float:
    score = 50.0

    if opportunity.asking_price_usd <= 0:
        return 0.0

    if (
        opportunity.annual_sde_usd is not None
        and opportunity.annual_sde_usd > 0
    ):
        multiple = (
            opportunity.asking_price_usd
            / opportunity.annual_sde_usd
        )

        if multiple <= 2.0:
            score += 35.0
        elif multiple <= 3.0:
            score += 25.0
        elif multiple <= 4.0:
            score += 10.0
        elif multiple <= 5.0:
            score -= 10.0
        else:
            score -= 30.0

    else:
        score -= 25.0

    return _clamp(score)


def screen_opportunity(
    opportunity: VenturesOpportunity,
) -> ScreeningScore:
    financial_quality = _score_financial_quality(
        opportunity
    )
    automation_potential = _score_automation_potential(
        opportunity
    )
    owner_independence = _score_owner_independence(
        opportunity
    )
    operational_simplicity = _score_operational_simplicity(
        opportunity
    )
    strategic_fit = _score_strategic_fit(
        opportunity
    )
    risk_quality = _score_risk_quality(
        opportunity
    )

    weights = {
        "financial_quality": 0.25,
        "automation_potential": 0.20,
        "owner_independence": 0.15,
        "operational_simplicity": 0.10,
        "strategic_fit": 0.15,
        "risk_quality": 0.15,
    }

    overall_score = (
        financial_quality * weights["financial_quality"]
        + automation_potential
        * weights["automation_potential"]
        + owner_independence
        * weights["owner_independence"]
        + operational_simplicity
        * weights["operational_simplicity"]
        + strategic_fit * weights["strategic_fit"]
        + risk_quality * weights["risk_quality"]
    )

    rationale: list[str] = []

    if automation_potential >= 80:
        rationale.append(
            "High automation potential."
        )

    if owner_independence >= 80:
        rationale.append(
            "Low owner workload dependency."
        )

    if financial_quality >= 80:
        rationale.append(
            "Strong basic financial profile."
        )

    if risk_quality < 40:
        rationale.append(
            "Acquisition economics require caution."
        )

    hard_reject_reasons: list[str] = []

    if (
        opportunity.annual_sde_usd is not None
        and opportunity.annual_sde_usd > 0
    ):
        asking_multiple = (
            opportunity.asking_price_usd
            / opportunity.annual_sde_usd
        )

        if asking_multiple > 8.0:
            hard_reject_reasons.append(
                "Asking price exceeds 8x annual SDE."
            )

    if (
        opportunity.owner_hours_per_week is not None
        and opportunity.owner_hours_per_week > 50
        and automation_potential < 75
    ):
        hard_reject_reasons.append(
            "Extreme owner workload with insufficient automation potential."
        )

    if financial_quality < 25:
        hard_reject_reasons.append(
            "Financial quality is below the minimum Ventures threshold."
        )

    rationale.extend(hard_reject_reasons)

    if hard_reject_reasons:
        recommendation = ScreeningRecommendation.REJECT
    elif overall_score >= 75:
        recommendation = ScreeningRecommendation.RESEARCH
    elif overall_score >= 55:
        recommendation = ScreeningRecommendation.WATCH
    else:
        recommendation = ScreeningRecommendation.REJECT

    return ScreeningScore(
        financial_quality=round(financial_quality, 2),
        automation_potential=round(
            automation_potential,
            2,
        ),
        owner_independence=round(
            owner_independence,
            2,
        ),
        operational_simplicity=round(
            operational_simplicity,
            2,
        ),
        strategic_fit=round(strategic_fit, 2),
        risk_quality=round(risk_quality, 2),
        overall_score=round(overall_score, 2),
        recommendation=recommendation,
        rationale=tuple(rationale),
    )
