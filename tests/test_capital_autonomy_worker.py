"""End-to-end revision worker tests with temporary stores."""

import fcntl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_capital_autonomy_scheduler as scheduler_fixtures

from app.agents import capital_worker, tasks
from app.capital.autonomy_policy import CapitalOperatingPolicy


class CapitalWorkerTests(unittest.TestCase):
    def setUp(self):
        self.environment = scheduler_fixtures.CapitalSchedulerTests()
        self.addCleanup(self.environment.doCleanups)
        self.environment.setUp()

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

        mocked = patch(
            "app.agents.capital_worker.read_operating_policy",
            return_value=CapitalOperatingPolicy(),
        )
        self.policy = mocked.start()
        self.addCleanup(mocked.stop)

    def run_cycle(self):
        return capital_worker.run_once(directory=self.root)

    def test_cycle_selects_and_completes_research(self):
        result = self.run_cycle()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["processed_count"], 1)
        self.assertFalse(result["live_capital_authorized"])

        saved_tasks = tasks.get_tasks()
        self.assertEqual(len(saved_tasks), 1)
        self.assertEqual(saved_tasks[0].status, tasks.TaskStatus.COMPLETED)

        research = self.environment.environment.fixture.read()
        self.assertEqual(len(research["candidates"]), 2)
        self.assertEqual(
            research["candidates"]["research_parent"]["status"],
            "archived",
        )

    def test_second_cycle_does_not_repeat_completed_revision(self):
        self.run_cycle()
        result = self.run_cycle()
        self.assertEqual(result["status"], "idle")
        self.assertEqual(result["processed_count"], 0)
        self.assertEqual(len(tasks.get_tasks()), 1)
        self.environment.environment.fixture.model.assert_called_once()

    def test_disabled_cycle_does_not_schedule(self):
        self.policy.return_value = CapitalOperatingPolicy(enabled=False)
        result = self.run_cycle()
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(len(tasks.get_tasks()), 0)
        self.environment.environment.fixture.model.assert_not_called()

    def test_paused_cycle_does_not_schedule(self):
        self.policy.return_value = CapitalOperatingPolicy(paused=True)
        result = self.run_cycle()
        self.assertEqual(result["status"], "paused")
        self.assertEqual(len(tasks.get_tasks()), 0)

    def test_existing_process_lock_prevents_execution(self):
        with (self.root / "worker.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_cycle()
            self.assertEqual(result["status"], "busy")
            self.assertEqual(len(tasks.get_tasks()), 0)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def test_failed_cycle_releases_process_lock(self):
        with patch(
            "app.agents.capital_worker._cycle",
            side_effect=RuntimeError("Test failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.run_cycle()

        # A separate file descriptor must now be able to acquire the lock.
        with (self.root / "worker.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def test_model_failure_is_recorded_without_candidate_creation(self):
        fixture = self.environment.environment.fixture
        fixture.model.side_effect = RuntimeError("Model unavailable")
        with self.assertRaises(RuntimeError):
            self.run_cycle()

        saved_tasks = tasks.get_tasks()
        self.assertEqual(len(saved_tasks), 1)
        self.assertEqual(saved_tasks[0].status, tasks.TaskStatus.FAILED)
        self.assertEqual(len(fixture.read()["candidates"]), 1)

    def test_cycle_processes_at_most_one_task(self):
        with patch(
            "app.agents.capital_worker.schedule_research_revision",
            return_value={"status": "work_pending", "scheduled_count": 0},
        ):
            runner_fixture = self.environment.environment
            first = runner_fixture.create()
            runner_fixture.create()

            result = self.run_cycle()

        self.assertEqual(result["processed_count"], 1)
        self.assertEqual(result["task_id"], first.task_id)
        statuses = [task.status for task in tasks.get_tasks()]
        self.assertEqual(statuses.count(tasks.TaskStatus.COMPLETED), 1)
        self.assertEqual(statuses.count(tasks.TaskStatus.QUEUED), 1)


if __name__ == "__main__":
    unittest.main()
