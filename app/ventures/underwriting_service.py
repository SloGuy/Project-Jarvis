from __future__ import annotations

from math import isfinite

from app.ventures.models import VenturesOpportunity
from app.ventures.underwriting_models import (
    UnderwritingAssumptions,
    UnderwritingScenario,
    VenturesUnderwritingReport,
)


def _build_scenario(
    *,
    name: str,
    revenue: float,
    sde: float,
    owner_hours: float,
    investment: float,
    assumptions: UnderwritingAssumptions,
    revenue_decline_percent: float,
) -> UnderwritingScenario:
    revenue_loss = revenue * revenue_decline_percent / 100
    scenario_revenue = revenue - revenue_loss
    scenario_sde = sde - revenue_loss

    remaining_hours = (
        owner_hours - assumptions.automation_hours_saved_per_week
    )
    labor_cost = (
        remaining_hours * 52 * assumptions.owner_hourly_cost_usd
    )
    cash_flow = (
        scenario_sde
        - assumptions.annual_added_operating_costs_usd
        - labor_cost
    )

    results = (
        scenario_revenue,
        scenario_sde,
        remaining_hours,
        labor_cost,
        cash_flow,
    )
    if not all(isfinite(value) for value in results):
        raise ValueError("Scenario calculation exceeded numeric limits.")

    cash_return = (
        cash_flow / investment * 100
        if investment > 0 else None
    )
    payback = (
        investment / cash_flow
        if investment > 0 and cash_flow > 0 else None
    )
    for value in (cash_return, payback):
        if value is not None and not isfinite(value):
            raise ValueError("Scenario ratio exceeded numeric limits.")

    return UnderwritingScenario(
        name=name,
        annual_revenue_usd=round(scenario_revenue, 2),
        annual_sde_usd=round(scenario_sde, 2),
        remaining_owner_hours_per_week=round(remaining_hours, 2),
        annual_owner_labor_cost_usd=round(labor_cost, 2),
        annual_adjusted_cash_flow_usd=round(cash_flow, 2),
        cash_return_percent=(
            round(cash_return, 2) if cash_return is not None else None
        ),
        payback_years=(
            round(payback, 2) if payback is not None else None
        ),
    )


def build_underwriting_report(
    opportunity: VenturesOpportunity,
    assumptions: UnderwritingAssumptions,
    *,
    research_record: dict | None = None,
) -> VenturesUnderwritingReport:
    inputs = {
        "asking_price_usd": opportunity.asking_price_usd,
        "annual_revenue_usd": opportunity.annual_revenue_usd,
        "annual_sde_usd": opportunity.annual_sde_usd,
        "owner_hours_per_week": opportunity.owner_hours_per_week,
    }
    missing = tuple(
        key for key, value in inputs.items() if value is None
    )

    for key, value in inputs.items():
        if value is None:
            continue
        if not isfinite(value):
            raise ValueError(f"{key} must be finite.")
        if key != "annual_sde_usd" and value < 0:
            raise ValueError(f"{key} must be nonnegative.")

    if opportunity.asking_price_usd is None:
        raise ValueError("Asking price is required.")

    hours = opportunity.owner_hours_per_week
    if (
        hours is not None
        and assumptions.automation_hours_saved_per_week > hours
    ):
        raise ValueError(
            "Automation hours saved cannot exceed owner workload."
        )

    investment = (
        opportunity.asking_price_usd
        + assumptions.acquisition_costs_usd
        + assumptions.working_capital_usd
    )
    if not isfinite(investment):
        raise ValueError("Total investment exceeded numeric limits.")

    sde = opportunity.annual_sde_usd
    multiple = (
        opportunity.asking_price_usd / sde
        if sde is not None and sde > 0 else None
    )
    if multiple is not None and not isfinite(multiple):
        raise ValueError("Asking-price multiple exceeded numeric limits.")

    research_date = None
    research_recommendation = None
    if research_record is not None:
        report = research_record["report"]
        if (
            research_record["opportunity_id"]
            != opportunity.opportunity_id
            or report["opportunity_id"] != opportunity.opportunity_id
        ):
            raise ValueError("Research belongs to another opportunity.")
        research_date = research_record["created_at"]
        research_recommendation = report["recommendation"]

    limitations = [
        "Scenarios use reported opportunity figures; research evidence "
        "does not automatically replace or verify those figures.",
        "Automation savings are assumptions, not demonstrated results.",
        "Owner labor is valued at the supplied hourly replacement cost "
        "for 52 weeks per year.",
        "Added operating costs must include automation expenses and "
        "exclude costs already reflected in SDE or owner labor.",
        "Downside revenue loss reduces SDE dollar-for-dollar; "
        "other costs and owner workload remain fixed.",
        "Cash flow is an annual SDE-based estimate before financing, "
        "taxes, capital expenditure, and future working-capital changes.",
        "Payback is a simple estimate assuming constant annual cash flow; "
        "it excludes resale value and recovery of working capital.",
        "This report grants no acquisition or lifecycle-transition authority.",
    ]

    if research_record is None:
        limitations.append("No research report was supplied.")
    elif research_recommendation != "ready_for_underwriting":
        limitations.append(
            "Research has not recommended readiness for underwriting; "
            "these calculations are preliminary."
        )

    scenarios = ()
    if missing:
        limitations.append(
            "Scenarios were not calculated because required "
            "opportunity inputs are missing."
        )
    else:
        scenarios = tuple(
            _build_scenario(
                name=name,
                revenue=opportunity.annual_revenue_usd,
                sde=opportunity.annual_sde_usd,
                owner_hours=opportunity.owner_hours_per_week,
                investment=investment,
                assumptions=assumptions,
                revenue_decline_percent=decline,
            )
            for name, decline in (
                ("base", 0.0),
                (
                    "downside",
                    assumptions.downside_revenue_decline_percent,
                ),
            )
        )

    return VenturesUnderwritingReport(
        opportunity_id=opportunity.opportunity_id,
        research_created_at=research_date,
        research_recommendation=research_recommendation,
        assumptions=assumptions,
        total_investment_usd=round(investment, 2),
        asking_price_to_sde_multiple=(
            round(multiple, 2) if multiple is not None else None
        ),
        scenarios=scenarios,
        missing_inputs=missing,
        limitations=tuple(limitations),
    )
