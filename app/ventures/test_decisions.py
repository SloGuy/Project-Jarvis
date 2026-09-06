from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ventures import api
from app.ventures import decision_service as service
from app.ventures import decision_store as store
from app.ventures.models import (
    BusinessType,
    OpportunityStatus,
    VenturesOpportunity,
    utc_now,
)
from app.ventures.underwriting_models import UnderwritingAssumptions
from app.ventures.underwriting_service import build_underwriting_report


def main():
    now = utc_now()
    opportunity = VenturesOpportunity(
        opportunity_id="decision_test",
        name="Synthetic Decision Test",
        business_type=BusinessType.MICRO_SAAS,
        asking_price_usd=100_000,
        annual_revenue_usd=100_000,
        annual_sde_usd=50_000,
        owner_hours_per_week=20,
        source="synthetic_test",
        source_url=None,
        notes="Test fixture",
        status=OpportunityStatus.COMMITTEE,
        created_at=now,
        updated_at=now,
    )
    research = {
        "opportunity_id": opportunity.opportunity_id,
        "created_at": now.isoformat(),
        "report": {
            "opportunity_id": opportunity.opportunity_id,
            "recommendation": "ready_for_underwriting",
        },
    }
    assumptions = UnderwritingAssumptions(
        acquisition_costs_usd=5_000,
        working_capital_usd=5_000,
        annual_added_operating_costs_usd=5_000,
        owner_hourly_cost_usd=25,
        automation_hours_saved_per_week=10,
        downside_revenue_decline_percent=20,
        notes="Synthetic assumptions.",
    )
    underwriting = {
        "opportunity_id": opportunity.opportunity_id,
        "created_at": now.isoformat(),
        "report": build_underwriting_report(
            opportunity,
            assumptions,
            research_record=research,
        ).to_dict(),
    }
    original = deepcopy((opportunity.to_dict(), research, underwriting))

    app = FastAPI()
    app.include_router(api.router)
    path = "/ventures/opportunities/decision_test/decisions"
    payload = {
        "decision": "approve",
        "recorded_by": "Test reviewer",
        "rationale": "Synthetic review only.",
    }

    with TemporaryDirectory() as directory:
        root = Path(directory)
        with (
            patch.object(store, "DECISION_DIRECTORY", root),
            patch.object(store, "DECISION_FILE", root / "decisions.json"),
            patch.object(
                service, "get_opportunity", return_value=opportunity
            ) as get_opportunity,
            patch.object(
                api, "get_opportunity", return_value=opportunity
            ),
            patch.object(
                service, "get_latest_research_report",
                return_value=research,
            ) as get_research,
            patch.object(
                service, "list_underwriting_reports",
                return_value=[underwriting],
            ) as get_underwriting,
            patch.object(api, "update_opportunity_status") as transition,
            TestClient(app) as client,
        ):
            def blocked(expected_status, body=None):
                before = store.list_decisions()
                response = client.post(
                    path, json=payload if body is None else body
                )
                assert response.status_code == expected_status, response.text
                assert store.list_decisions() == before

            from dataclasses import replace

            get_opportunity.return_value = replace(
                opportunity, status=OpportunityStatus.RESEARCH
            )
            blocked(409)
            get_opportunity.return_value = opportunity

            get_research.return_value = None
            blocked(409)

            unready = deepcopy(research)
            unready["report"]["recommendation"] = "more_research_required"
            get_research.return_value = unready
            blocked(409)
            get_research.return_value = research

            get_underwriting.return_value = []
            blocked(409)

            stale = deepcopy(underwriting)
            stale["report"]["research_created_at"] = "older_research"
            get_underwriting.return_value = [stale]
            blocked(409)

            incomplete = deepcopy(underwriting)
            incomplete["report"]["missing_inputs"] = ["annual_sde_usd"]
            get_underwriting.return_value = [incomplete]
            blocked(409)

            no_scenarios = deepcopy(underwriting)
            no_scenarios["report"]["scenarios"] = []
            get_underwriting.return_value = [no_scenarios]
            blocked(409)
            get_underwriting.return_value = [underwriting]
            print("approval_prerequisites: PASS")

            for changes in (
                {"recorded_by": " "},
                {"rationale": " "},
                {"decision": "purchase"},
                {"acquisition_authority": True},
            ):
                blocked(422, dict(payload, **changes))

            get_opportunity.return_value = None
            blocked(404)
            get_opportunity.return_value = opportunity
            print("invalid_decisions_no_write: PASS")

            response = client.post(path, json=payload)
            assert response.status_code == 200, response.text
            approved = response.json()
            assert approved["decision"] == "approve"
            assert approved["research_created_at"] == research["created_at"]
            assert (
                approved["underwriting_created_at"]
                == underwriting["created_at"]
            )
            assert approved["reviewer_identity_verified"] is False
            assert approved["acquisition_authority"] is False
            assert approved["capital_deployment_authority"] is False
            assert approved["opportunity_status_at_review"] == "committee"
            print("review_approval_recorded: PASS")

            expected = [approved]
            for decision in ("reject", "defer", "more_research"):
                response = client.post(
                    path, json=dict(payload, decision=decision)
                )
                assert response.status_code == 200, response.text
                expected.append(response.json())

            response = client.get(path)
            assert response.status_code == 200, response.text
            assert response.json()["decisions"] == expected
            assert response.json()["count"] == 4
            assert len({item["decision_id"] for item in expected}) == 4
            transition.assert_not_called()
            assert (
                opportunity.to_dict(), research, underwriting
            ) == original
            print("decision_history_and_authority_boundaries: PASS")


if __name__ == "__main__":
    main()
