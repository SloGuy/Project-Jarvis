from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.ventures import discovery_collector as collector
from app.ventures import discovery_store as store
from app.ventures import opportunities
from app.ventures.models import OpportunityStatus


def snapshot():
    listing = {
        "id": "synthetic_saas",
        "listing_number": 123,
        "listing_status": "For Sale",
        "public_title": "Synthetic SaaS",
        "listing_price": 50_000,
        "unpriced": False,
        "average_monthly_gross_revenue": 5_000,
        "average_monthly_net_profit": 2_000,
        "hours_worked_per_week": 10,
        "monetizations": [{"monetization": "SaaS"}],
    }
    return {
        "source": "empire_flippers",
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:00:01+00:00",
        "total_reported": 1,
        "fetched_count": 1,
        "pages_fetched": 1,
        "page_sources": [],
        "listings": [listing],
    }


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        with (
            patch.object(
                opportunities, "VENTURES_STATE_DIRECTORY", root
            ),
            patch.object(
                opportunities, "OPPORTUNITIES_FILE",
                root / "opportunities.json",
            ),
            patch.object(
                store, "DISCOVERY_DIRECTORY", root / "discovery"
            ),
            patch.object(
                collector, "fetch_all_listings",
                return_value=snapshot(),
            ) as fetch,
        ):
            first = collector.collect_discovery()
            assert first["status"] == "success"
            assert first["linked_count"] == 1

            items = opportunities.list_opportunities()
            assert len(items) == 1
            item = items[0]
            assert item.status == OpportunityStatus.DISCOVERED
            assert item.business_type.value == "saas"
            assert item.annual_revenue_usd is None
            assert item.annual_sde_usd is None
            assert item.owner_hours_per_week == 10

            runs = store.list_discovery_runs()
            assert len(runs) == 1
            assert runs[0]["snapshot"] == snapshot()
            assert runs[0]["automatic_purchase_authority"] is False
            print("intake_and_source_preservation: PASS")

            opportunities.update_opportunity_status(
                item.opportunity_id, OpportunityStatus.SCREENING
            )
            before = opportunities.get_opportunity(
                item.opportunity_id
            ).to_dict()

            changed = snapshot()
            changed["listings"][0]["listing_price"] = 60_000
            fetch.return_value = changed
            second = collector.collect_discovery()

            assert second["status"] == "success"
            assert len(opportunities.list_opportunities()) == 1
            assert opportunities.get_opportunity(
                item.opportunity_id
            ).to_dict() == before
            runs = store.list_discovery_runs()
            assert len(runs) == 2
            assert any(
                run["snapshot"] == changed for run in runs
            )
            print("repeat_import_preserves_reviewed_opportunity: PASS")

            invalid = snapshot()
            invalid["listings"][0]["id"] = "unpriced_listing"
            invalid["listings"][0]["unpriced"] = True
            fetch.return_value = invalid
            skipped = collector.collect_discovery()
            assert skipped["status"] == "success"
            assert skipped["skipped_count"] == 1
            assert len(opportunities.list_opportunities()) == 1
            print("unpriced_listing_skipped: PASS")

            before_runs = len(store.list_discovery_runs())
            fetch.side_effect = TimeoutError("Synthetic timeout")
            failed = collector.collect_discovery()
            assert failed["status"] == "failed"
            assert failed["processed_count"] == 0
            assert failed["fetched_count"] is None
            assert "TimeoutError" in failed["error"]
            assert len(store.list_discovery_runs()) == before_runs + 1
            assert len(opportunities.list_opportunities()) == 1
            print("failed_fetch_recorded_without_intake: PASS")
            fetch.side_effect = None

            partial = snapshot()
            extra = deepcopy(partial["listings"][0])
            extra["id"] = "second_listing"
            extra["listing_number"] = 456
            partial["listings"].append(extra)
            partial["total_reported"] = 2
            partial["fetched_count"] = 2
            fetch.return_value = partial

            real_import = collector.import_listing
            calls = 0

            def fail_second(listing):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("Synthetic intake failure")
                return real_import(listing)

            with patch.object(
                collector, "import_listing", side_effect=fail_second
            ):
                failed = collector.collect_discovery()

            assert failed["status"] == "failed"
            assert failed["processed_count"] == 1
            assert failed["linked_count"] == 1
            failed_record = next(
                run for run in store.list_discovery_runs()
                if run["run_id"] == failed["run_id"]
            )
            assert failed_record["snapshot"] == partial
            assert len(failed_record["intake_results"]) == 1

            retried = collector.collect_discovery()
            assert retried["status"] == "success"
            assert len(opportunities.list_opportunities()) == 2
            print("partial_failure_and_duplicate_safe_retry: PASS")

            before_runs = len(store.list_discovery_runs())
            fetch.reset_mock()
            with store.discovery_run_lock():
                try:
                    collector.collect_discovery()
                except RuntimeError as exc:
                    assert "already active" in str(exc)
                else:
                    raise AssertionError("Expected overlapping run rejection")
            fetch.assert_not_called()
            assert len(store.list_discovery_runs()) == before_runs
            print("overlapping_collector_blocked: PASS")

            empty = snapshot()
            empty.update(
                total_reported=0,
                fetched_count=0,
                listings=[],
            )
            fetch.return_value = empty
            result = collector.collect_discovery()
            assert result["status"] == "success"
            assert result["fetched_count"] == 0
            assert result["processed_count"] == 0
            assert len(opportunities.list_opportunities()) == 2
            print("empty_scan_preserves_existing_opportunities: PASS")


if __name__ == "__main__":
    main()
