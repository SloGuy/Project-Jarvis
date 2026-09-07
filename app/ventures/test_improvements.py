from copy import deepcopy

from app.ventures.improvement_models import ImprovementEconomics
from app.ventures.improvement_service import build_improvement_proposals


def fixture():
    return {
        "opportunity_id": "improvement_test",
        "created_at": "2026-01-01T00:00:00+00:00",
        "report": {
            "opportunity_id": "improvement_test",
            "claims": [{
                "claim_id": "work_test",
                "category": "source_work_required",
                "claim": (
                    "Providing general customer service; managing marketing; "
                    "maintaining web app / infrastructure."
                ),
                "evidence_status": "unverified",
            }],
        },
    }


def expect_invalid(action):
    try:
        action()
    except ValueError:
        return
    raise AssertionError("Expected ValueError")


def main():
    research = fixture()
    original = deepcopy(research)
    proposals = build_improvement_proposals(research)

    assert len(proposals) == 3
    assert len({item["proposal_id"] for item in proposals}) == 3
    for proposal in proposals:
        assert proposal["source_claim_ids"] == ("work_test",)
        assert proposal["economics"] is None
        assert proposal["status"] == "proposed"
        assert proposal["implementation_authority"] is False
        assert proposal["automatic_purchase_authority"] is False
        assert proposal["capital_transfer_authority"] is False
        assert proposal["proposed_workflow"]
        assert proposal["validation_steps"]
        assert proposal["human_oversight"]
    assert build_improvement_proposals(research) == proposals
    assert research == original
    print("source_matching_stable_ids_and_authority: PASS")

    for changes in (
        {"category": "source_summary"},
        {"evidence_status": "contradicted"},
        {"claim": "Packing physical orders."},
    ):
        changed = fixture()
        changed["report"]["claims"][0].update(changes)
        assert build_improvement_proposals(changed) == []

    empty = fixture()
    empty["report"]["claims"] = []
    assert build_improvement_proposals(empty) == []
    print("unsupported_and_contradicted_tasks_excluded: PASS")

    mismatch = fixture()
    mismatch["report"]["opportunity_id"] = "another"
    expect_invalid(lambda: build_improvement_proposals(mismatch))

    duplicate = fixture()
    duplicate["report"]["claims"] *= 2
    expect_invalid(lambda: build_improvement_proposals(duplicate))

    revised = fixture()
    revised["created_at"] = "2026-01-02T00:00:00+00:00"
    revised_ids = {
        item["proposal_id"] for item in build_improvement_proposals(revised)
    }
    assert revised_ids.isdisjoint({
        item["proposal_id"] for item in proposals
    })
    print("research_identity_and_version_tracking: PASS")

    assumptions = dict(
        implementation_cost_usd=1_000,
        monthly_operating_cost_usd=100,
        hours_saved_per_month=10,
        labor_value_per_hour_usd=25,
        monthly_incremental_gross_profit_usd=50,
        assumptions_notes="Synthetic scenario, not established savings.",
    )
    economics = ImprovementEconomics(**assumptions).to_dict()
    assert economics["monthly_owner_time_value_usd"] == 250
    assert economics["monthly_cash_effect_before_implementation_usd"] == -50
    assert economics["monthly_combined_economic_value_usd"] == 200
    assert economics["implementation_cost_usd"] == 1_000
    assert economics["basis"] == "scenario_assumptions_not_verified_savings"
    print("cash_effect_separate_from_owner_time: PASS")

    for changes in (
        {"implementation_cost_usd": -1},
        {"hours_saved_per_month": True},
        {"monthly_operating_cost_usd": float("nan")},
        {"labor_value_per_hour_usd": float("inf")},
        {"assumptions_notes": " "},
    ):
        expect_invalid(
            lambda: ImprovementEconomics(**dict(assumptions, **changes))
        )

    overflow = ImprovementEconomics(**dict(
        assumptions,
        hours_saved_per_month=1e308,
        labor_value_per_hour_usd=1e308,
    ))
    expect_invalid(overflow.to_dict)
    print("invalid_assumptions_and_overflow_rejected: PASS")


if __name__ == "__main__":
    main()
