from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.ventures import discovery_screening_store as store
from app.ventures import discovery_screening_service as service
from app.ventures.discovery_screening import (
    evaluate_discovered_opportunity,
)
from app.ventures.models import (
    BusinessType,
    OpportunityStatus,
    VenturesOpportunity,
    utc_now,
)


def main():
    now = utc_now()
    opportunity = VenturesOpportunity(
        opportunity_id="screening_test",
        name="Synthetic SaaS",
        business_type=BusinessType.SAAS,
        asking_price_usd=50_000,
        annual_revenue_usd=None,
        annual_sde_usd=None,
        owner_hours_per_week=10,
        source="empire_flippers:synthetic",
        source_url="https://example.invalid/123",
        notes="Synthetic fixture.",
        status=OpportunityStatus.DISCOVERED,
        created_at=now,
        updated_at=now,
    )

    evaluation = evaluate_discovered_opportunity(opportunity)
    assert evaluation["disposition"] == "research_candidate"
    assert set(evaluation["missing_inputs"]) == {
        "annual_revenue_usd", "annual_sde_usd"
    }
    assert evaluation["advisory_only"] is True
    assert evaluation["automatic_purchase_authority"] is False

    for changes, expected in (
        ({"annual_sde_usd": -1}, "deprioritized"),
        ({"annual_revenue_usd": 0}, "deprioritized"),
        ({"annual_sde_usd": 1_000}, "deprioritized"),
        ({"asking_price_usd": float("nan")}, "needs_data_review"),
        ({"business_type": BusinessType.OTHER},
         "needs_classification_review"),
    ):
        result = evaluate_discovered_opportunity(
            replace(opportunity, **changes)
        )
        assert result["disposition"] == expected, result
    print("missing_evidence_and_screening_routes: PASS")

    run = {
        "run_id": "synthetic_run_1",
        "source": "empire_flippers",
        "status": "success",
        "snapshot": {"listings": [{"id": "synthetic"}]},
        "intake_results": [{
            "outcome": "linked",
            "source_id": "synthetic",
            "opportunity_id": opportunity.opportunity_id,
        }],
    }

    with TemporaryDirectory() as directory:
        root = Path(directory)
        with (
            patch.object(store, "SCREENING_STATE_DIRECTORY", root),
            patch.object(
                store, "SCREENING_STATE_FILE", root / "screening.json"
            ),
            patch.object(
                service, "get_opportunity", return_value=opportunity
            ) as get_opportunity,
        ):
            first = service.screen_discovery_run(run)
            assert first["pending_count"] == 1
            assert first["changed_count"] == 1
            queued_at = store.list_research_queue()[0]["queued_at"]

            second = service.screen_discovery_run(
                dict(run, run_id="synthetic_run_2")
            )
            assert second["changed_count"] == 0
            assert len(store.list_discovery_evaluations()) == 1
            assert len(store.list_research_queue()) == 1
            assert store.list_research_queue()[0]["queued_at"] == queued_at
            print("repeat_scan_queue_deduplication: PASS")

            before = store.SCREENING_STATE_FILE.read_bytes()
            try:
                service.screen_discovery_run(dict(run, status="failed"))
            except ValueError:
                pass
            else:
                raise AssertionError("Failed scan must be rejected")
            assert store.SCREENING_STATE_FILE.read_bytes() == before
            print("failed_scan_preserves_queue: PASS")

            empty = dict(
                run,
                run_id="synthetic_empty",
                snapshot={"listings": []},
                intake_results=[],
            )
            result = service.screen_discovery_run(empty)
            assert result["deactivated_count"] == 1
            assert store.list_research_queue()[0]["status"] == "inactive"

            service.screen_discovery_run(run)
            entry = store.list_research_queue()[0]
            assert entry["status"] == "pending"
            assert "inactive_reason" not in entry
            assert entry["queued_at"] == queued_at
            print("disappearance_and_reactivation: PASS")

            for status in (
                OpportunityStatus.WATCH,
                OpportunityStatus.REJECTED,
                OpportunityStatus.APPROVED,
                OpportunityStatus.UNDERWRITING,
                OpportunityStatus.COMMITTEE,
            ):
                get_opportunity.return_value = replace(
                    opportunity, status=status
                )
                service.screen_discovery_run(run)
                assert store.list_research_queue()[0]["status"] == "inactive"
            print("existing_lifecycle_decisions_respected: PASS")

            get_opportunity.return_value = opportunity
            service.screen_discovery_run(run)
            before = store.SCREENING_STATE_FILE.read_bytes()

            get_opportunity.return_value = replace(
                opportunity, source="another_source"
            )
            try:
                service.screen_discovery_run(run)
            except ValueError:
                pass
            else:
                raise AssertionError("Source mismatch must be rejected")
            assert store.SCREENING_STATE_FILE.read_bytes() == before
            assert opportunity.status == OpportunityStatus.DISCOVERED
            print("source_validation_and_no_lifecycle_writes: PASS")


if __name__ == "__main__":
    main()
