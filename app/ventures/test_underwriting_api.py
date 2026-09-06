from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ventures import api
from app.ventures import underwriting_store as store
from app.ventures.models import (
    BusinessType,
    OpportunityStatus,
    VenturesOpportunity,
    utc_now,
)


def main():
    now = utc_now()
    opportunity = VenturesOpportunity(
        opportunity_id="underwriting_api_test",
        name="Synthetic API Test",
        business_type=BusinessType.MICRO_SAAS,
        asking_price_usd=100_000,
        annual_revenue_usd=100_000,
        annual_sde_usd=50_000,
        owner_hours_per_week=20,
        source="synthetic_test",
        source_url=None,
        notes="Test fixture",
        status=OpportunityStatus.RESEARCH,
        created_at=now,
        updated_at=now,
    )
    original = opportunity.to_dict()
    payload = {
        "acquisition_costs_usd": 5_000,
        "working_capital_usd": 5_000,
        "annual_added_operating_costs_usd": 5_000,
        "owner_hourly_cost_usd": 25,
        "automation_hours_saved_per_week": 10,
        "downside_revenue_decline_percent": 20,
        "notes": "Synthetic API test assumptions.",
    }

    app = FastAPI()
    app.include_router(api.router)
    path = (
        "/ventures/opportunities/"
        + opportunity.opportunity_id
        + "/underwriting"
    )

    with TemporaryDirectory() as directory:
        root = Path(directory)
        with (
            patch.object(store, "UNDERWRITING_DIRECTORY", root),
            patch.object(
                store, "UNDERWRITING_FILE", root / "reports.json"
            ),
            patch.object(
                api, "get_opportunity", return_value=opportunity
            ),
            patch.object(
                api, "get_latest_research_report", return_value=None
            ),
            patch.object(api, "update_opportunity_status") as transition,
            patch.object(api, "save_research_report") as research_write,
            TestClient(app) as client,
        ):
            response = client.post(path, json=payload)
            assert response.status_code == 200, response.text
            first = response.json()
            report = first["report"]
            assert report["total_investment_usd"] == 110_000
            assert (
                report["scenarios"][0]["annual_adjusted_cash_flow_usd"]
                == 32_000
            )
            assert report["advisory_only"] is True
            assert report["human_approval_required"] is True
            assert report["acquisition_authority"] is False
            print("http_report_creation: PASS")

            response = client.post(
                path,
                json=dict(payload, downside_revenue_decline_percent=30),
            )
            assert response.status_code == 200, response.text
            second = response.json()
            assert (
                second["report"]["scenarios"][1][
                    "annual_adjusted_cash_flow_usd"
                ] == 2_000
            )

            response = client.get(path)
            assert response.status_code == 200, response.text
            history = response.json()
            assert history["count"] == 2
            assert history["reports"] == [first, second]
            print("http_history_preservation: PASS")

            for changes in (
                {"notes": " "},
                {"downside_revenue_decline_percent": 101},
                {"automation_hours_saved_per_week": 21},
                {"acquisition_authority": True},
            ):
                response = client.post(
                    path, json=dict(payload, **changes)
                )
                assert response.status_code == 422, response.text

            assert client.get(path).json() == history
            transition.assert_not_called()
            research_write.assert_not_called()
            assert opportunity.to_dict() == original
            print("http_validation_and_authority_boundaries: PASS")


if __name__ == "__main__":
    main()
