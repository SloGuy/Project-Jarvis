from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.ventures import assessment_runner as runner
from app.ventures import assessment_store as store
from app.ventures import research_store
from app.ventures.models import (
    BusinessType,
    OpportunityStatus,
    VenturesOpportunity,
    utc_now,
)


def main():
    now = utc_now()
    opportunity = VenturesOpportunity(
        opportunity_id="assessment_runner_test",
        name="Synthetic SaaS",
        business_type=BusinessType.SAAS,
        asking_price_usd=50_000,
        annual_revenue_usd=None,
        annual_sde_usd=None,
        owner_hours_per_week=10,
        source="synthetic",
        source_url="https://example.invalid/test",
        notes="Synthetic fixture.",
        status=OpportunityStatus.RESEARCH,
        created_at=now,
        updated_at=now,
    )
    research = {
        "opportunity_id": opportunity.opportunity_id,
        "created_at": now.isoformat(),
        "report": {
            "opportunity_id": opportunity.opportunity_id,
            "claims": [],
            "summary": "Synthetic research version one.",
        },
    }

    def model_result(record):
        return {
            "opportunity_id": record["opportunity_id"],
            "research_created_at": record["created_at"],
            "model": runner.llm.ASSESSMENT_MODEL,
            "assessment": {
                "advisory_only": True,
                "automatic_purchase_authority": False,
            },
            "review_status": "unreviewed_model_draft",
        }

    def expect_error(action, error_type=ValueError):
        try:
            action()
        except error_type:
            return
        raise AssertionError(f"Expected {error_type.__name__}")

    with TemporaryDirectory() as directory:
        root = Path(directory)
        with (
            patch.object(store, "ASSESSMENT_DIRECTORY", root / "drafts"),
            patch.object(research_store, "RESEARCH_DIRECTORY", root),
            patch.object(
                research_store, "RESEARCH_FILE", root / "research.json"
            ),
            patch.object(
                runner, "get_opportunity", return_value=opportunity
            ) as get_opportunity,
            patch.object(
                runner, "get_latest_research_report", return_value=research
            ) as get_research,
            patch.object(
                runner.llm, "assess_research_report",
                side_effect=model_result,
            ) as model,
        ):
            original = deepcopy(research)
            first = runner.assess_opportunity(opportunity.opportunity_id)
            assert first["status"] == "created"
            assert first["record"]["research_snapshot"] == original

            second = runner.assess_opportunity(opportunity.opportunity_id)
            assert second["status"] == "cached"
            assert second["record"] == first["record"]
            assert model.call_count == 1
            assert len(store.list_assessments()) == 1
            assert research == original
            print("saved_draft_and_cache: PASS")

            revised = deepcopy(research)
            revised["report"]["summary"] = "Synthetic version two."
            get_research.return_value = revised
            third = runner.assess_opportunity(opportunity.opportunity_id)
            assert third["status"] == "created"
            assert third["record"]["assessment_key"] != (
                first["record"]["assessment_key"]
            )
            assert len(store.list_assessments()) == 2

            with patch.object(runner, "INPUT_VERSION", "synthetic_v2"):
                result = runner.assess_opportunity(opportunity.opportunity_id)
                assert result["status"] == "created"
            assert len(store.list_assessments()) == 3
            print("research_and_configuration_versioning: PASS")

            pending = deepcopy(revised)
            pending["report"]["summary"] = "Version before generation."
            changed = deepcopy(pending)
            changed["report"]["summary"] = "Version after generation."
            get_research.side_effect = [pending, changed]
            before = len(store.list_assessments())
            expect_error(
                lambda: runner.assess_opportunity(opportunity.opportunity_id)
            )
            assert len(store.list_assessments()) == before
            get_research.side_effect = None
            get_research.return_value = pending
            print("research_change_during_generation_rejected: PASS")

            from dataclasses import replace

            get_opportunity.side_effect = [
                opportunity,
                replace(opportunity, notes="Changed during generation."),
            ]
            expect_error(
                lambda: runner.assess_opportunity(opportunity.opportunity_id)
            )
            assert len(store.list_assessments()) == before
            get_opportunity.side_effect = None
            get_opportunity.return_value = opportunity
            print("opportunity_change_during_generation_rejected: PASS")

            model.side_effect = TimeoutError("Synthetic timeout")
            expect_error(
                lambda: runner.assess_opportunity(opportunity.opportunity_id),
                TimeoutError,
            )
            assert len(store.list_assessments()) == before
            model.side_effect = model_result
            print("model_failure_no_saved_draft: PASS")

            model.reset_mock()
            with runner._runner_lock():
                expect_error(
                    lambda: runner.assess_opportunity(
                        opportunity.opportunity_id
                    ),
                    RuntimeError,
                )
            model.assert_not_called()
            print("overlapping_assessment_blocked: PASS")

            get_opportunity.return_value = replace(
                opportunity, status=OpportunityStatus.APPROVED
            )
            expect_error(
                lambda: runner.assess_opportunity(opportunity.opportunity_id)
            )
            model.assert_not_called()

            expect_error(lambda: store.get_assessment("../invalid"))
            assert opportunity.status == OpportunityStatus.RESEARCH
            print("lifecycle_and_storage_path_validation: PASS")


if __name__ == "__main__":
    main()
