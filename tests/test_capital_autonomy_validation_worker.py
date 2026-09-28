"""Automatic validation selection tests with isolated stores."""

from datetime import timedelta
import unittest
from unittest.mock import patch

import test_capital_autonomy_validation_draft as fixtures
import test_capital_autonomy_validation_registration as registration_fixtures

from app.capital import autonomy_validation_worker as worker
from app.capital import research_store
from app.capital import validation_registry as registry
from app.capital.autonomy_policy import CapitalOperatingPolicy


class CapitalValidationWorkerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CapitalValidationDraftTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

        with research_store.locked_research_state(write=True) as state:
            state["candidates"]["research_parent"]["status"] = "proposed"

        self.policy = self.mock(
            "read_operating_policy",
            return_value=CapitalOperatingPolicy(),
        )
        self.prepare = self.mock("prepare_validation_draft")
        self.register = self.mock(
            "register_saved_validation",
            return_value={"plan_id": "validation_new"},
        )
        self.evaluate = self.mock(
            "process_validation",
            return_value={"status": "outcome_recorded"},
        )

        self.review = self.mock(
            "review_validation_outcome",
            return_value={"status": "research_review_recorded"},
        )

    def mock(self, name, **kwargs):
        mocked = patch.object(worker, name, **kwargs)
        result = mocked.start()
        self.addCleanup(mocked.stop)
        return result

    def plan(self, status="registered", sealed=False):
        row = self.fixture.registration.register(self.fixture.plan)
        with registry.locked_state(write=True) as state:
            saved = state["plans"][row["plan_id"]]
            saved["status"] = status
            if sealed:
                saved["witness_collection"] = {"status": "sealed"}
        return row

    def run_cycle(self):
        return worker.run_validation_cycle()

    def test_unattempted_candidate_gets_stable_work_identity(self):
        result = self.run_cycle()
        self.assertEqual(result["status"], "registered")
        self.prepare.assert_called_once_with(
            task_id="validation:research_parent:1",
            research_id="research_parent",
        )
        self.register.assert_called_once_with(
            task_id="validation:research_parent:1",
        )
        self.assertFalse(result["live_capital_authorized"])

    def test_disabled_worker_does_not_start_work(self):
        self.policy.return_value = CapitalOperatingPolicy(enabled=False)
        self.assertEqual(self.run_cycle()["status"], "disabled")
        self.prepare.assert_not_called()
        self.evaluate.assert_not_called()

    def test_paused_worker_does_not_start_work(self):
        self.policy.return_value = CapitalOperatingPolicy(paused=True)
        self.assertEqual(self.run_cycle()["status"], "paused")
        self.prepare.assert_not_called()
        self.evaluate.assert_not_called()

    def test_existing_registration_occupies_capacity(self):
        row = self.plan()
        result = self.run_cycle()
        self.assertEqual(result["status"], "validation_in_progress")
        self.assertEqual(result["plans"][0]["plan_id"], row["plan_id"])
        self.prepare.assert_not_called()
        self.evaluate.assert_not_called()

    def test_sealed_finished_plan_is_evaluated(self):
        row = self.plan(sealed=True)
        self.fixture.registration.clock.return_value = (
            registration_fixtures.NOW + timedelta(days=7)
        )
        self.assertEqual(self.run_cycle()["status"], "outcome_recorded")
        self.evaluate.assert_called_once_with(row["plan_id"])
        self.prepare.assert_not_called()

    def test_running_plan_is_not_reexecuted(self):
        self.plan(status="running")
        self.assertEqual(
            self.run_cycle()["status"], "validation_in_progress"
        )
        self.evaluate.assert_not_called()

    def test_failed_attempt_is_not_replaced(self):
        self.plan(status="failed")
        self.assertEqual(
            self.run_cycle()["status"], "no_new_validation_work"
        )
        self.prepare.assert_not_called()
        self.evaluate.assert_not_called()

    def test_completed_plan_with_missing_outcome_is_processed(self):
        row = self.plan(status="completed")
        self.assertEqual(self.run_cycle()["status"], "outcome_recorded")
        self.evaluate.assert_called_once_with(row["plan_id"])

    def test_recorded_outcome_is_reviewed_once(self):
        row = self.plan(status="completed")
        marker = {
            "plan_id": row["plan_id"],
            "plan_sha256": row["registered_sha256"],
        }
        with research_store.locked_research_state(write=True) as state:
            candidate = state["candidates"]["research_parent"]
            candidate["validation_assessments"] = [dict(marker)]
            candidate["validation_recommendations"] = [dict(marker)]

        self.assertEqual(
            self.run_cycle()["status"], "research_review_recorded"
        )
        self.review.assert_called_once_with(row["plan_id"])
        self.evaluate.assert_not_called()

        with research_store.locked_research_state(write=True) as state:
            state["review_requests"] = {
                f"capital-review:{row['plan_id']}": {"fixture": True}
            }
        self.review.reset_mock()
        self.assertEqual(
            self.run_cycle()["status"], "no_new_validation_work"
        )
        self.review.assert_not_called()
        self.prepare.assert_not_called()

    def test_revision_required_candidate_is_not_registered(self):
        with research_store.locked_research_state(write=True) as state:
            state["candidates"]["research_parent"]["status"] = (
                "revision_required"
            )
        self.assertEqual(
            self.run_cycle()["status"], "no_new_validation_work"
        )
        self.prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
