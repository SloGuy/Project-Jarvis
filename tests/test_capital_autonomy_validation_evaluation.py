"""Evaluation dispatch tests using an isolated registry."""

import fcntl
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_capital_autonomy_validation_registration as fixtures

from app.capital import autonomy_validation_evaluation as worker
from app.capital.autonomy_policy import (
    CapitalOperatingPolicy,
    CapitalPolicyError,
)


class CapitalValidationEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ValidationRegistrationRetryTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.row = self.fixture.register()
        self.plan_id = self.row["plan_id"]
        self.fixture.clock.return_value = fixtures.NOW + timedelta(days=7)

        self.checkpoint = {"count": 20, "sealed": True}
        self.row["witness_collection"] = {
            "status": "sealed",
            "store_checkpoint": self.checkpoint,
        }

        self.mock(worker.registry, "get_plan", return_value=self.row)
        self.policy = self.mock(
            worker,
            "read_operating_policy",
            return_value=CapitalOperatingPolicy(),
        )
        self.validate = self.mock(worker, "validate_collection")
        self.store = SimpleNamespace(
            read_checkpoint=lambda: self.checkpoint
        )
        self.mock(worker, "store_for", return_value=self.store)

        def execute(plan_id):
            self.row["status"] = "completed"

        self.execute = self.mock(
            worker, "execute_registered", side_effect=execute
        )
        self.attach = self.mock(
            worker,
            "attach_completed",
            return_value={
                "research": {"research_id": "research_test"},
                "assessment": {"validation_status": "insufficient_evidence"},
            },
        )
        self.recommend = self.mock(
            worker,
            "record_recommendation",
            return_value={"recommendation": "REVISE"},
        )

    def mock(self, target, name, **kwargs):
        mocked = patch.object(target, name, **kwargs)
        result = mocked.start()
        self.addCleanup(mocked.stop)
        return result

    def process(self):
        return worker.process_validation(self.plan_id)

    def test_sealed_finished_window_executes_and_records(self):
        result = self.process()
        self.assertEqual(result["status"], "outcome_recorded")
        self.assertEqual(result["recommendation"], "REVISE")
        self.assertEqual(result["validation_status"], "insufficient_evidence")
        self.assertFalse(result["promotion_authorized"])
        self.execute.assert_called_once_with(self.plan_id)

    def test_completed_plan_is_not_executed_again(self):
        self.row["status"] = "completed"
        self.assertEqual(self.process()["status"], "outcome_recorded")
        self.execute.assert_not_called()
        self.attach.assert_called_once()

    def test_deadline_is_enforced(self):
        self.fixture.clock.return_value = fixtures.NOW
        self.assertEqual(self.process()["status"], "waiting_for_deadline")
        self.execute.assert_not_called()
        self.attach.assert_not_called()

    def test_open_collection_waits_for_sealing(self):
        self.row["witness_collection"]["status"] = "collecting"
        self.assertEqual(self.process()["status"], "waiting_for_seal")
        self.execute.assert_not_called()

    def test_running_and_failed_plans_are_not_restarted(self):
        for status in ("running", "failed"):
            with self.subTest(status=status):
                self.row["status"] = status
                self.assertEqual(self.process()["status"], "blocked")
                self.assertEqual(self.row["status"], status)
        self.execute.assert_not_called()
        self.attach.assert_not_called()

    def test_checkpoint_mismatch_blocks_execution(self):
        self.store.read_checkpoint = lambda: {"different": True}
        with self.assertRaises(ValueError):
            self.process()
        self.execute.assert_not_called()

    def test_pause_blocks_new_evaluation(self):
        self.policy.return_value = CapitalOperatingPolicy(paused=True)
        with self.assertRaises(CapitalPolicyError):
            self.process()
        self.execute.assert_not_called()

    def test_partial_recording_can_resume_without_execution(self):
        self.recommend.side_effect = [
            RuntimeError("Interrupted recommendation write"),
            {"recommendation": "REVISE"},
        ]
        with self.assertRaises(RuntimeError):
            self.process()
        self.assertEqual(self.row["status"], "completed")
        self.assertEqual(self.process()["status"], "outcome_recorded")
        self.execute.assert_called_once()
        self.assertEqual(self.attach.call_count, 2)

    def test_manual_plan_is_not_taken_over(self):
        self.row["envelope"]["plan"]["created_by"] = "operator"
        with self.assertRaises(ValueError):
            self.process()
        self.execute.assert_not_called()

    def test_existing_process_lock_prevents_execution(self):
        path = worker.registry.DIRECTORY / "autonomy-evaluation.lock"
        with path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.process()["status"], "busy")
            self.execute.assert_not_called()
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    unittest.main()
