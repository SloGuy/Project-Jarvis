from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from app.ventures import assessment_worker as worker
from app.ventures import assessment_attempt_store as attempts
from app.ventures import assessment_store as store


def main():
    now = datetime.now(timezone.utc)
    configuration = {"model": "qwen3:8b", "test_version": 1}

    reports = {
        name: {
            "opportunity_id": name,
            "created_at": now.isoformat(),
            "report": {"opportunity_id": name},
        }
        for name in ("one", "two")
    }
    queue = [
        {
            "opportunity_id": name,
            "status": "collected",
            "discovery_run_id": "run_test",
            "evaluation_fingerprint": "evaluation_test",
            "collected_evaluation_fingerprint": "evaluation_test",
        }
        for name in reports
    ]
    run = {
        "run_id": "run_test",
        "source": "empire_flippers",
        "status": "success",
        "snapshot": {
            "finished_at": now.isoformat(),
            "listings": [{"id": name} for name in reports],
        },
        "intake_results": [
            {
                "outcome": "linked",
                "opportunity_id": name,
                "source_id": name,
            }
            for name in reports
        ],
    }

    def opportunity(name):
        return SimpleNamespace(
            opportunity_id=name,
            source=f"empire_flippers:{name}",
            status=SimpleNamespace(value="research"),
        )

    def key_for(name):
        return store.assessment_key(
            research_record=reports[name],
            configuration=configuration,
        )

    cached = {}

    def assess(name, *, expected_assessment_key=None):
        assert expected_assessment_key == key_for(name)
        result = {"assessment_key": key_for(name)}
        cached[key_for(name)] = result
        return {"status": "created", "record": result}

    def expect_rejected():
        try:
            worker.run_assessment_worker()
        except ValueError:
            return
        raise AssertionError("Expected scan rejection")

    with TemporaryDirectory() as directory:
        root = Path(directory)
        with (
            patch.object(attempts, "ATTEMPT_DIRECTORY", root),
            patch.object(
                worker, "assessment_configuration",
                return_value=configuration,
            ),
            patch.object(
                worker, "list_discovery_runs", return_value=[run]
            ) as get_runs,
            patch.object(
                worker, "list_research_queue", return_value=queue
            ) as get_queue,
            patch.object(
                worker, "get_opportunity", side_effect=opportunity
            ),
            patch.object(
                worker, "get_latest_research_report",
                side_effect=lambda name: deepcopy(reports[name]),
            ),
            patch.object(
                store, "get_assessment",
                side_effect=lambda key: cached.get(key),
            ),
            patch.object(
                worker, "assess_opportunity", side_effect=assess
            ) as model,
        ):
            first = worker.run_assessment_worker()
            assert first["opportunity_id"] == "one"
            assert first["status"] == "success"
            assert model.call_count == 1
            assert len(attempts.list_attempts()) == 1

            second = worker.run_assessment_worker()
            assert second["opportunity_id"] == "two"
            assert second["cached_skipped"] == 1
            assert model.call_count == 2

            third = worker.run_assessment_worker()
            assert third["outcome"] == "no_candidate_due"
            assert third["cached_skipped"] == 2
            assert model.call_count == 2
            print("one_candidate_limit_and_cached_skips: PASS")

            cached.clear()
            model.side_effect = TimeoutError("Synthetic timeout")
            failed = worker.run_assessment_worker()
            assert failed["status"] == "failed"
            assert failed["opportunity_id"] == "one"
            assert attempts.list_attempts()[-1]["status"] == "failed"

            model.side_effect = assess
            continued = worker.run_assessment_worker()
            assert continued["opportunity_id"] == "two"
            assert continued["retry_delayed"] == 1
            print("failure_delay_does_not_block_next_candidate: PASS")

            failed_attempt = next(
                item for item in reversed(attempts.list_attempts())
                if item["opportunity_id"] == "one"
                and item["status"] == "failed"
            )
            old = (
                datetime.now(timezone.utc) - timedelta(hours=7)
            ).isoformat()
            attempts._save(dict(
                failed_attempt, started_at=old, finished_at=old
            ))
            retried = worker.run_assessment_worker()
            assert retried["opportunity_id"] == "one"
            assert retried["status"] == "success"
            print("retry_after_delay: PASS")

            cached.clear()
            pending = attempts.start_attempt(
                opportunity_id="one", assessment_key=key_for("one")
            )
            get_queue.return_value = [queue[0]]
            model.reset_mock()
            result = worker.run_assessment_worker()
            assert result["retry_delayed"] == 1
            model.assert_not_called()
            print("interrupted_attempt_delayed: PASS")

            attempts._save(dict(pending, started_at=old))
            get_queue.side_effect = [
                [queue[0]],
                [dict(queue[0], status="inactive")],
            ]
            result = worker.run_assessment_worker()
            assert result["status"] == "deferred"
            model.assert_not_called()
            assert attempts.list_attempts()[-1]["status"] == "deferred"
            get_queue.side_effect = None
            get_queue.return_value = queue
            print("changed_queue_defers_generation: PASS")

            before = len(attempts.list_attempts())
            stale = deepcopy(run)
            stale["snapshot"]["finished_at"] = (
                datetime.now(timezone.utc) - timedelta(hours=13)
            ).isoformat()
            get_runs.return_value = [stale]
            expect_rejected()

            get_runs.return_value = [dict(run, status="failed")]
            expect_rejected()

            future = deepcopy(run)
            future["snapshot"]["finished_at"] = (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat()
            get_runs.return_value = [future]
            expect_rejected()

            assert len(attempts.list_attempts()) == before
            model.assert_not_called()
            print("stale_failed_and_future_scans_rejected: PASS")

            get_runs.return_value = [run]
            with worker._worker_lock():
                try:
                    worker.run_assessment_worker()
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("Expected worker lock rejection")
            model.assert_not_called()
            print("overlapping_worker_blocked: PASS")


if __name__ == "__main__":
    main()
