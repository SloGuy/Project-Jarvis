from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ventures.api import router
from app.ventures import opportunities
from app.ventures import screening_store
from app.ventures import research_store
from app.ventures import underwriting_store
from app.ventures import decision_store


def main():
    app = FastAPI()
    app.include_router(router)

    stores = (
        (opportunities, "VENTURES_STATE_DIRECTORY",
         "OPPORTUNITIES_FILE", "opportunities.json"),
        (screening_store, "SCREENING_DIRECTORY",
         "SCREENING_FILE", "screenings.json"),
        (research_store, "RESEARCH_DIRECTORY",
         "RESEARCH_FILE", "research.json"),
        (underwriting_store, "UNDERWRITING_DIRECTORY",
         "UNDERWRITING_FILE", "underwriting.json"),
        (decision_store, "DECISION_DIRECTORY",
         "DECISION_FILE", "decisions.json"),
    )

    with TemporaryDirectory() as directory, ExitStack() as stack:
        root = Path(directory)
        for module, directory_key, file_key, filename in stores:
            stack.enter_context(patch.object(module, directory_key, root))
            stack.enter_context(
                patch.object(module, file_key, root / filename)
            )

        client = stack.enter_context(TestClient(app))

        def post(path, data=None, expected=200):
            response = client.post(path, json=data)
            assert response.status_code == expected, response.text
            return response.json()

        def get(path):
            response = client.get(path)
            assert response.status_code == 200, response.text
            return response.json()

        opportunity = post("/ventures/opportunities", {
            "name": "Synthetic V1 Pipeline",
            "business_type": "micro_saas",
            "asking_price_usd": 100_000,
            "annual_revenue_usd": 100_000,
            "annual_sde_usd": 50_000,
            "owner_hours_per_week": 20,
            "source": "isolated_test",
            "notes": "Synthetic fixture, not real acquisition evidence.",
        })
        path = "/ventures/opportunities/" + opportunity["opportunity_id"]
        assert opportunity["status"] == "discovered"

        post(path + "/status", {"status": "committee"}, expected=409)
        assert get(path)["status"] == "discovered"

        post(path + "/status", {"status": "screening"})
        screening = post(path + "/screen")
        assert get(path + "/screenings")["screenings"] == [screening]
        assert get(path)["status"] == "screening"
        print("intake_screening_and_lifecycle: PASS")

        post(path + "/status", {"status": "research"})
        initial = post(path + "/research")
        assert post(path + "/research") == initial

        for claim in initial["report"]["claims"]:
            latest = post(
                path + "/research/claims/" + claim["claim_id"] + "/evidence",
                {
                    "evidence_status": "supported",
                    "evidence_quality": "high",
                    "evidence_notes": "Synthetic evidence for isolated test.",
                },
            )

        assert latest["report"]["evidence_score"] == 100
        assert (
            latest["report"]["recommendation"]
            == "ready_for_underwriting"
        )
        assert (
            latest["report"]["diligence_questions"]
            == initial["report"]["diligence_questions"]
        )
        assert latest["report"]["risks"] == initial["report"]["risks"]
        assert get(path)["status"] == "research"
        print("research_evidence_and_preservation: PASS")

        assumptions = {
            "acquisition_costs_usd": 5_000,
            "working_capital_usd": 5_000,
            "annual_added_operating_costs_usd": 5_000,
            "owner_hourly_cost_usd": 25,
            "automation_hours_saved_per_week": 10,
            "downside_revenue_decline_percent": 20,
            "notes": "Synthetic assumptions.",
        }
        post(path + "/status", {"status": "underwriting"})
        underwriting = post(path + "/underwriting", assumptions)
        report = underwriting["report"]
        assert report["research_created_at"] == latest["created_at"]
        assert report["scenarios"][0]["annual_adjusted_cash_flow_usd"] == 32_000
        assert report["scenarios"][1]["annual_adjusted_cash_flow_usd"] == 12_000
        assert get(path)["status"] == "underwriting"
        print("underwriting_from_research: PASS")

        decision = {
            "decision": "approve",
            "recorded_by": "Synthetic reviewer",
            "rationale": "Isolated pipeline verification.",
        }
        post(path + "/decisions", decision, expected=409)
        assert get(path + "/decisions")["count"] == 0

        post(path + "/status", {"status": "committee"})

        # A new research revision makes the prior underwriting stale.
        claim = latest["report"]["claims"][0]
        post(
            path + "/research/claims/" + claim["claim_id"] + "/evidence",
            {
                "evidence_status": "supported",
                "evidence_quality": "high",
                "evidence_notes": "New synthetic research revision.",
            },
        )
        post(path + "/decisions", decision, expected=409)
        assert get(path + "/decisions")["count"] == 0

        refreshed = post(path + "/underwriting", assumptions)
        assert get(path + "/underwriting")["reports"] == [
            underwriting, refreshed
        ]
        print("stale_underwriting_blocks_approval: PASS")

        before = get(path)
        research_before = get(path + "/research")
        underwriting_before = get(path + "/underwriting")
        recorded = post(path + "/decisions", decision)

        assert recorded["decision"] == "approve"
        assert recorded["acquisition_authority"] is False
        assert recorded["capital_deployment_authority"] is False
        assert recorded["underwriting_created_at"] == refreshed["created_at"]
        assert get(path + "/decisions")["decisions"] == [recorded]
        assert get(path) == before
        assert get(path + "/research") == research_before
        assert get(path + "/underwriting") == underwriting_before
        print("review_decision_without_state_transition: PASS")

    print("ventures_v1_pipeline: PASS")


if __name__ == "__main__":
    main()
