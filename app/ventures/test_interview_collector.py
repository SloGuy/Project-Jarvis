from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import nullcontext
import json

from app.ventures import interview_collector as collector


def run_tests():
    source_id = "source_test"
    opportunity_id = "venture_interview_test"
    valid_url = "https://www.youtube.com/watch?v=1yR4C9kFG0I"

    listing = {
        "id": source_id,
        "seller_interview_link": valid_url,
    }
    run = {
        "run_id": "discovery_test",
        "snapshot": {"listings": [listing]},
        "intake_results": [{
            "outcome": "linked",
            "opportunity_id": opportunity_id,
            "source_id": source_id,
        }],
    }
    queue = [{
        "opportunity_id": opportunity_id,
        "status": "collected",
        "discovery_run_id": "discovery_test",
    }]
    opportunity = SimpleNamespace(
        opportunity_id=opportunity_id,
        source=f"empire_flippers:{source_id}",
        status=SimpleNamespace(value="research"),
    )
    segments = [{
        "text": "The owner handles customer support.",
        "start": 12.0,
        "duration": 4.0,
    }]

    with TemporaryDirectory() as temporary:
        directory = Path(temporary)

        with (
            patch.object(collector, "DIRECTORY", directory),
            patch.object(
                collector, "discovery_run_lock",
                side_effect=nullcontext,
            ),
            patch.object(
                collector, "_latest_scan", return_value=run,
            ),
            patch.object(
                collector, "list_research_queue", return_value=queue,
            ),
            patch.object(
                collector, "get_opportunity",
                return_value=opportunity,
            ),
            patch.object(collector, "YouTubeTranscriptApi") as api,
        ):
            transcript = api.return_value.fetch.return_value
            transcript.to_raw_data.return_value = segments
            transcript.language_code = "en"
            transcript.is_generated = True

            listing["seller_interview_link"] = (
                "https://example.com/watch?v=1yR4C9kFG0I"
            )
            result = collector.collect_one_interview()
            assert result["status"] == "no_interview_due"
            api.assert_not_called()
            assert not list(directory.glob("interview_*.json"))
            print("invalid_source_url_no_request: PASS")

            listing["seller_interview_link"] = valid_url
            api.return_value.fetch.side_effect = TimeoutError("test")

            result = collector.collect_one_interview()
            assert result["status"] == "unavailable"
            assert api.return_value.fetch.call_count == 1

            collector.collect_one_interview()
            assert api.return_value.fetch.call_count == 1
            print("failed_collection_retry_delay: PASS")

            path = next(directory.glob("interview_*.json"))
            record = json.loads(path.read_text())
            record["collected_at"] = (
                datetime.now(timezone.utc) - timedelta(hours=7)
            ).isoformat()
            path.write_text(json.dumps(record))

            api.return_value.fetch.side_effect = None
            result = collector.collect_one_interview()
            assert result["status"] == "collected", result
            assert api.return_value.fetch.call_count == 2

            saved = collector.list_interviews(opportunity_id)
            assert len(saved) == 1
            assert saved[0]["segments"] == segments
            assert saved[0]["source_listing_id"] == source_id
            assert saved[0]["source_url"] == valid_url
            assert saved[0]["evidence_status"] == "unverified"
            assert saved[0]["auto_generated"] is True
            print("retry_success_and_source_preservation: PASS")

            before = path.read_bytes()
            collector.collect_one_interview()
            assert path.read_bytes() == before
            assert api.return_value.fetch.call_count == 2
            print("successful_cache_no_duplicate_fetch: PASS")

            queue[0]["status"] = "inactive"
            collector.collect_one_interview()
            assert api.return_value.fetch.call_count == 2
            assert path.read_bytes() == before
            assert opportunity.status.value == "research"
            print("inactive_queue_and_lifecycle_preserved: PASS")


if __name__ == "__main__":
    run_tests()
