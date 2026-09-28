"""Bounded recovery tests using temporary task and research stores."""

import unittest
from datetime import datetime, timedelta, timezone

import test_capital_autonomy_runner as runner_fixtures

from app.agents import tasks
from app.agents.capital_recovery import recover_capital_research_tasks
from app.agents.capital_registry import RESEARCH_AGENT_ID


NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


class CapitalRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.environment = runner_fixtures.CapitalRunnerTests()
        self.addCleanup(self.environment.doCleanups)
        self.environment.setUp()

    def task(self, *, status="failed", attempts=1, age=120, error=None):
        task = self.environment.create()
        stamp = (NOW - timedelta(seconds=age)).isoformat()
        with tasks._state_lock():
            state = tasks._load_state_unlocked()
            row = state["tasks"][task.task_id]
            row.update({
                "status": status,
                "execution_attempts": attempts,
                "started_at": stamp,
                "heartbeat_at": stamp,
                "completed_at": stamp if status == "failed" else None,
                "error": error or "ResearchModelError: Request failed.",
            })
            tasks._save_state_unlocked(state)
        return task

    def current(self, task):
        return self.environment.current(task)

    def recover(self):
        return recover_capital_research_tasks(now=NOW)

    def test_transient_failure_requeues_same_identity(self):
        task = self.task()
        result = self.recover()
        self.assertEqual(result["recovered_task_ids"], [task.task_id])
        current = self.current(task)
        self.assertEqual(current.status, tasks.TaskStatus.QUEUED)
        self.assertEqual(current.execution_attempts, 1)
        self.assertEqual(current.recovery_attempts, 1)
        self.assertIsNone(current.error)
        self.assertIsNone(current.completed_at)

    def test_retry_delay_is_respected(self):
        task = self.task(age=59)
        self.assertEqual(self.recover()["recovered_task_ids"], [])
        self.assertEqual(self.current(task).status, tasks.TaskStatus.FAILED)

    def test_nontransient_failure_is_not_retried(self):
        task = self.task(error="ValueError: Parent changed.")
        self.assertEqual(self.recover()["recovered_task_ids"], [])
        self.assertEqual(self.current(task).status, tasks.TaskStatus.FAILED)

    def test_fresh_running_task_is_not_recovered(self):
        task = self.task(status="running", age=299)
        self.assertEqual(self.recover()["recovered_task_ids"], [])
        self.assertEqual(self.current(task).status, tasks.TaskStatus.RUNNING)

    def test_stale_running_task_is_recovered(self):
        task = self.task(status="running", age=300)
        self.assertEqual(
            self.recover()["recovered_task_ids"], [task.task_id]
        )
        self.assertEqual(self.current(task).status, tasks.TaskStatus.QUEUED)

    def test_exhausted_failed_task_is_not_requeued(self):
        task = self.task(attempts=3)
        result = self.recover()
        self.assertEqual(result["recovered_task_ids"], [])
        self.assertEqual(result["exhausted_task_ids"], [task.task_id])
        self.assertEqual(self.current(task).status, tasks.TaskStatus.FAILED)

    def test_exhausted_queued_and_stale_tasks_are_failed(self):
        for status in ("queued", "running"):
            with self.subTest(status=status):
                task = self.task(status=status, attempts=3, age=600)
                result = self.recover()
                self.assertIn(task.task_id, result["exhausted_task_ids"])
                current = self.current(task)
                self.assertEqual(current.status, tasks.TaskStatus.FAILED)
                self.assertEqual(current.error, "Capital retry budget exhausted.")

    def test_other_agents_are_untouched(self):
        task = self.task(status="running", age=600)
        with tasks._state_lock():
            state = tasks._load_state_unlocked()
            state["tasks"][task.task_id]["assigned_agent_id"] = (
                "engineering.software_engineer"
            )
            tasks._save_state_unlocked(state)
        self.assertEqual(self.recover()["recovered_task_ids"], [])
        self.assertEqual(self.current(task).status, tasks.TaskStatus.RUNNING)

    def test_repeated_recovery_does_not_duplicate_history(self):
        task = self.task()
        self.recover()
        self.recover()
        with tasks._state_lock():
            state = tasks._load_state_unlocked()
        history = state["capital_retry_history"][task.task_id]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["reason"], "transient_failure")
        self.assertEqual(self.current(task).recovery_attempts, 1)

    def test_claim_enforces_attempt_limit_atomically(self):
        task = self.task(status="queued", attempts=3)
        with self.assertRaises(ValueError):
            tasks.claim_task(
                task_id=task.task_id,
                agent_id=RESEARCH_AGENT_ID,
                maximum_execution_attempts=3,
            )
        current = self.current(task)
        self.assertEqual(current.status, tasks.TaskStatus.QUEUED)
        self.assertEqual(current.execution_attempts, 3)


if __name__ == "__main__":
    unittest.main()
