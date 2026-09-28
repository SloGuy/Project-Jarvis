"""Task attempt ownership tests using isolated task storage."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents import tasks
from app.agents.capital_registry import RESEARCH_AGENT_ID


class CapitalTaskAttemptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)

        for name, value in (
            ("STATE_DIRECTORY", root),
            ("TASK_STATE_FILE", root / "tasks.json"),
            ("TASK_LOCK_FILE", root / "tasks.lock"),
        ):
            mocked = patch.object(tasks, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def claimed(self):
        task = tasks.create_task(
            title="Test Capital task",
            objective="Test attempt ownership.",
            assigned_agent_id=RESEARCH_AGENT_ID,
        )
        return tasks.claim_task(
            task_id=task.task_id,
            agent_id=RESEARCH_AGENT_ID,
        )

    def invoke(self, operation, task, attempt):
        arguments = {
            "task_id": task.task_id,
            "expected_execution_attempt": attempt,
        }
        if operation == "heartbeat":
            return tasks.heartbeat_task(
                agent_id=RESEARCH_AGENT_ID, **arguments
            )
        if operation == "complete":
            return tasks.complete_task(result="Completed", **arguments)
        return tasks.fail_task(error="Test failure", **arguments)

    def test_current_attempt_can_update_task(self):
        for operation in ("heartbeat", "complete", "fail"):
            with self.subTest(operation=operation):
                task = self.claimed()
                result = self.invoke(
                    operation, task, task.execution_attempts
                )
                expected = {
                    "heartbeat": tasks.TaskStatus.RUNNING,
                    "complete": tasks.TaskStatus.COMPLETED,
                    "fail": tasks.TaskStatus.FAILED,
                }[operation]
                self.assertEqual(result.status, expected)

    def test_old_attempt_cannot_update_new_attempt(self):
        for operation in ("heartbeat", "complete", "fail"):
            with self.subTest(operation=operation):
                task = self.claimed()

                # Simulate the persisted state after recovery and reclaim.
                with tasks._state_lock():
                    state = tasks._load_state_unlocked()
                    state["tasks"][task.task_id]["execution_attempts"] += 1
                    tasks._save_state_unlocked(state)

                with tasks._state_lock():
                    before = tasks._load_state_unlocked()

                with self.assertRaises(ValueError):
                    self.invoke(operation, task, task.execution_attempts)

                with tasks._state_lock():
                    after = tasks._load_state_unlocked()
                self.assertEqual(after, before)

    def test_invalid_attempt_numbers_are_rejected(self):
        for operation in ("heartbeat", "complete", "fail"):
            for attempt in (True, 0, -1, "1", 1.0):
                with self.subTest(operation=operation, attempt=attempt):
                    task = self.claimed()
                    with self.assertRaises(ValueError):
                        self.invoke(operation, task, attempt)

    def test_matching_attempt_does_not_bypass_task_status(self):
        task = self.claimed()
        tasks.complete_task(
            task_id=task.task_id,
            result="Done",
            expected_execution_attempt=task.execution_attempts,
        )
        for operation in ("heartbeat", "complete", "fail"):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    self.invoke(operation, task, task.execution_attempts)

    def test_matching_attempt_does_not_bypass_agent_identity(self):
        task = self.claimed()
        with self.assertRaises(ValueError):
            tasks.heartbeat_task(
                task_id=task.task_id,
                agent_id="capital.validation",
                expected_execution_attempt=task.execution_attempts,
            )

    def test_legacy_callers_can_omit_attempt(self):
        task = self.claimed()
        tasks.heartbeat_task(
            task_id=task.task_id,
            agent_id=RESEARCH_AGENT_ID,
        )
        completed = tasks.complete_task(
            task_id=task.task_id,
            result="Legacy completion",
        )
        self.assertEqual(completed.status, tasks.TaskStatus.COMPLETED)

        task = self.claimed()
        failed = tasks.fail_task(
            task_id=task.task_id,
            error="Legacy failure",
        )
        self.assertEqual(failed.status, tasks.TaskStatus.FAILED)



    def keyed_task(self, **overrides):
        arguments = {
            "title": "Scheduled research",
            "objective": "Investigate evidence.",
            "assigned_agent_id": RESEARCH_AGENT_ID,
            "request_key": "capital:test:revision",
        }
        arguments.update(overrides)
        return tasks.create_task(**arguments)

    def test_request_key_reuses_task(self):
        first = self.keyed_task()
        second = self.keyed_task()
        self.assertEqual(first.task_id, second.task_id)
        with tasks._state_lock():
            state = tasks._load_state_unlocked()
        self.assertEqual(len(state["tasks"]), 1)

    def test_conflicting_request_is_rejected(self):
        self.keyed_task()
        with self.assertRaises(ValueError):
            self.keyed_task(objective="Different work")

    def test_completed_request_is_not_requeued(self):
        original = self.keyed_task()
        claimed = tasks.claim_task(
            task_id=original.task_id, agent_id=RESEARCH_AGENT_ID
        )
        tasks.complete_task(
            task_id=claimed.task_id,
            result="Saved result",
            expected_execution_attempt=claimed.execution_attempts,
        )
        retried = self.keyed_task()
        self.assertEqual(retried.status, tasks.TaskStatus.COMPLETED)
        self.assertEqual(retried.result, "Saved result")
        self.assertEqual(retried.task_id, original.task_id)

if __name__ == "__main__":
    unittest.main()
