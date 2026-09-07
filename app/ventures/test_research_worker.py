from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.ventures import discovery_store
from app.ventures import discovery_screening_store as queue_store
from app.ventures import research_store
from app.ventures import research_worker as worker
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
        opportunity_id="worker_test",
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
    listing = {
        "id": "synthetic",
        "listing_price": 50_000,
        "average_monthly_net_profit": 2_000,
        "summary": "Synthetic subscription software.",
    }
    run = {
        "run_id": "worker_run_1",
        "source": "empire_flippers",
        "status": "success",
        "snapshot": {
            "finished_at": now.isoformat(),
            "listings": [listing],
        },
        "intake_results": [{
            "outcome": "linked",
            "opportunity_id": opportunity.opportunity_id,
            "source_id": "synthetic",
        }],
    }

    with TemporaryDirectory() as directory:
        root = Path(directory)
        with (
            patch.object(
                discovery_store, "DISCOVERY_DIRECTORY",
                root / "discovery",
            ),
            patch.object(
                queue_store, "SCREENING_STATE_DIRECTORY", root
            ),
            patch.object(
                queue_store, "SCREENING_STATE_FILE", root / "queue.json"
            ),
            patch.object(research_store, "RESEARCH_DIRECTORY", root),
            patch.object(
                research_store, "RESEARCH_FILE", root / "research.json"
            ),
            patch.object(
                worker, "get_opportunity", return_value=opportunity
            ),
            patch.object(
                worker, "list_discovery_runs", return_value=[run]
            ) as get_runs,
        ):
            def queue_for(run_id):
                queue_store.save_evaluation(
                    opportunity_snapshot=opportunity.to_dict(),
                    evaluation=evaluate_discovered_opportunity(opportunity),
                    discovery_run_id=run_id,
                )

            def expect_scan_rejected(bad_run):
                before_queue = queue_store.SCREENING_STATE_FILE.read_bytes()
                before_reports = research_store.RESEARCH_FILE.read_bytes()
                get_runs.return_value = [bad_run]
                try:
                    worker.collect_queued_research()
                except ValueError:
                    pass
                else:
                    raise AssertionError("Expected scan rejection")
                assert (
                    queue_store.SCREENING_STATE_FILE.read_bytes()
                    == before_queue
                )
                assert research_store.RESEARCH_FILE.read_bytes() == before_reports

            queue_for(run["run_id"])
            result = worker.collect_queued_research()
            assert result["status"] == "success"
            assert result["collected_count"] == 1
            assert result["independent_verification_performed"] is False
            entry = queue_store.list_research_queue()[0]
            assert entry["status"] == "collected"
            assert len(research_store.list_research_reports()) == 1
            print("queued_source_collection: PASS")

            result = worker.collect_queued_research()
            assert result["unchanged_count"] == 1
            assert len(research_store.list_research_reports()) == 1

            next_run = deepcopy(run)
            next_run["run_id"] = "worker_run_2"
            next_run["snapshot"]["finished_at"] = (
                datetime.now(timezone.utc).isoformat()
            )
            queue_for(next_run["run_id"])
            get_runs.return_value = [next_run]
            result = worker.collect_queued_research()
            assert result["unchanged_count"] == 1
            assert len(research_store.list_research_reports()) == 1
            print("unchanged_scans_do_not_duplicate_research: PASS")

            changed = deepcopy(next_run)
            changed["run_id"] = "worker_run_3"
            changed["snapshot"]["listings"][0][
                "average_monthly_net_profit"
            ] = 1_500
            queue_for(changed["run_id"])
            get_runs.return_value = [changed]

            with patch.object(
                worker, "merge_source_research",
                side_effect=RuntimeError("Synthetic merge failure"),
            ):
                result = worker.collect_queued_research()
            assert result["failed_count"] == 1
            assert queue_store.list_research_queue()[0]["status"] == (
                "collection_failed"
            )

            result = worker.collect_queued_research()
            assert result["collected_count"] == 1
            assert queue_store.list_research_queue()[0]["status"] == "collected"
            assert len(research_store.list_research_reports()) == 2
            assert worker.collect_queued_research()["unchanged_count"] == 1
            print("changed_source_and_failed_collection_retry: PASS")

            stale = deepcopy(changed)
            stale["snapshot"]["finished_at"] = (
                datetime.now(timezone.utc) - timedelta(hours=13)
            ).isoformat()
            expect_scan_rejected(stale)

            future = deepcopy(changed)
            future["snapshot"]["finished_at"] = (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat()
            expect_scan_rejected(future)

            expect_scan_rejected(dict(changed, status="failed"))
            print("stale_future_and_failed_scans_blocked: PASS")

            before = queue_store.SCREENING_STATE_FILE.read_bytes()
            accepted = queue_store.record_research_collection(
                opportunity_id=opportunity.opportunity_id,
                expected_discovery_run_id="outdated_run",
                expected_evaluation_fingerprint="outdated_evaluation",
                source_fingerprint="synthetic_fingerprint",
                report_created_at=now.isoformat(),
            )
            assert accepted is False
            assert queue_store.SCREENING_STATE_FILE.read_bytes() == before
            print("outdated_completion_rejected: PASS")

            queue_store.reconcile_research_queue(
                source="empire_flippers",
                active_opportunity_ids=set(),
                discovery_run_id="listing_disappeared",
            )
            get_runs.return_value = [changed]
            before_reports = research_store.RESEARCH_FILE.read_bytes()
            result = worker.collect_queued_research()
            assert result["collected_count"] == 0
            assert result["results"] == []
            assert research_store.RESEARCH_FILE.read_bytes() == before_reports
            assert opportunity.status == OpportunityStatus.DISCOVERED
            print("inactive_queue_and_lifecycle_preserved: PASS")


if __name__ == "__main__":
    main()
