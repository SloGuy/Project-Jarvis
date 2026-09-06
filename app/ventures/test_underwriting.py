from dataclasses import replace
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from app.ventures.models import (
    BusinessType,
    OpportunityStatus,
    VenturesOpportunity,
    utc_now,
)
from app.ventures.underwriting_models import UnderwritingAssumptions
from app.ventures.underwriting_service import build_underwriting_report
from app.ventures import underwriting_store as store


def expect_invalid(action):
    try:
        action()
    except ValueError:
        return
    raise AssertionError("Expected ValueError")


def main():
    now = utc_now()
    opportunity = VenturesOpportunity(
        opportunity_id="underwriting_test",
        name="Underwriting Test",
        business_type=BusinessType.MICRO_SAAS,
        asking_price_usd=100_000,
        annual_revenue_usd=100_000,
        annual_sde_usd=50_000,
        owner_hours_per_week=20,
        source="synthetic_test",
        source_url=None,
        notes="Synthetic inputs, not seller evidence.",
        status=OpportunityStatus.RESEARCH,
        created_at=now,
        updated_at=now,
    )
    assumptions = UnderwritingAssumptions(
        acquisition_costs_usd=5_000,
        working_capital_usd=5_000,
        annual_added_operating_costs_usd=5_000,
        owner_hourly_cost_usd=25,
        automation_hours_saved_per_week=10,
        downside_revenue_decline_percent=20,
        notes="Synthetic scenario assumptions.",
    )
    original = opportunity.to_dict()
    report = build_underwriting_report(opportunity, assumptions)
    base, downside = report.scenarios

    assert report.total_investment_usd == 110_000
    assert report.asking_price_to_sde_multiple == 2
    assert base.annual_revenue_usd == 100_000
    assert base.annual_sde_usd == 50_000
    assert base.remaining_owner_hours_per_week == 10
    assert base.annual_owner_labor_cost_usd == 13_000
    assert base.annual_adjusted_cash_flow_usd == 32_000
    assert base.cash_return_percent == 29.09
    assert base.payback_years == 3.44
    assert downside.annual_revenue_usd == 80_000
    assert downside.annual_sde_usd == 30_000
    assert downside.annual_adjusted_cash_flow_usd == 12_000
    assert downside.cash_return_percent == 10.91
    assert downside.payback_years == 9.17
    print("base_and_downside_math: PASS")

    for field in (
        "annual_revenue_usd",
        "annual_sde_usd",
        "owner_hours_per_week",
    ):
        incomplete = build_underwriting_report(
            replace(opportunity, **{field: None}), assumptions
        )
        assert incomplete.missing_inputs == (field,)
        assert incomplete.scenarios == ()
    print("missing_inputs_not_zeroed: PASS")

    loss = build_underwriting_report(
        replace(opportunity, annual_sde_usd=-1_000), assumptions
    )
    assert loss.asking_price_to_sde_multiple is None
    assert loss.scenarios[0].annual_adjusted_cash_flow_usd == -19_000
    assert loss.scenarios[0].payback_years is None

    zero = build_underwriting_report(
        replace(opportunity, asking_price_usd=0),
        replace(
            assumptions,
            acquisition_costs_usd=0,
            working_capital_usd=0,
        ),
    )
    assert zero.scenarios[0].cash_return_percent is None
    assert zero.scenarios[0].payback_years is None
    print("loss_and_zero_investment: PASS")

    for changes in (
        {"working_capital_usd": -1},
        {"owner_hourly_cost_usd": float("nan")},
        {"acquisition_costs_usd": float("inf")},
        {"downside_revenue_decline_percent": 101},
        {"notes": " "},
    ):
        expect_invalid(lambda: replace(assumptions, **changes))

    expect_invalid(
        lambda: build_underwriting_report(
            opportunity,
            replace(assumptions, automation_hours_saved_per_week=21),
        )
    )
    expect_invalid(
        lambda: build_underwriting_report(
            replace(opportunity, annual_revenue_usd=-1), assumptions
        )
    )
    print("invalid_inputs_rejected: PASS")

    research = {
        "opportunity_id": opportunity.opportunity_id,
        "created_at": now.isoformat(),
        "report": {
            "opportunity_id": opportunity.opportunity_id,
            "recommendation": "more_research_required",
        },
    }
    preliminary = build_underwriting_report(
        opportunity, assumptions, research_record=research
    )
    assert preliminary.research_created_at == now.isoformat()
    assert preliminary.research_recommendation == "more_research_required"
    assert any("preliminary" in text for text in preliminary.limitations)

    expect_invalid(
        lambda: build_underwriting_report(
            opportunity,
            assumptions,
            research_record=dict(research, opportunity_id="another"),
        )
    )
    data = preliminary.to_dict()
    assert data["advisory_only"] is True
    assert data["human_approval_required"] is True
    assert data["acquisition_authority"] is False
    assert opportunity.to_dict() == original
    print("research_link_and_authority_boundaries: PASS")

    with TemporaryDirectory() as directory:
        root = Path(directory)
        with (
            patch.object(store, "UNDERWRITING_DIRECTORY", root),
            patch.object(
                store, "UNDERWRITING_FILE", root / "reports.json"
            ),
        ):
            assert store.list_underwriting_reports() == []
            first = store.save_underwriting_report(report)
            second = store.save_underwriting_report(preliminary)
            assert store.list_underwriting_reports(
                opportunity.opportunity_id
            ) == [first, second]
            assert first["report"] == report.to_dict()
            assert second["report"] == preliminary.to_dict()
            assert store.list_underwriting_reports("another") == []
    print("isolated_report_history: PASS")


if __name__ == "__main__":
    main()
