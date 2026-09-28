"""Capital runner integration tests with isolated stores."""

import json
import unittest
from unittest.mock import patch

import test_capital_autonomy_research_draft as draft_fixtures

from app.agents import tasks, capital_runner
from app.agents.capital_registry import RESEARCH_AGENT_ID
from app.capital import research_store
from app.capital.autonomy_policy import (
    CapitalOperatingPolicy,
    CapitalPolicyError,
)


class CapitalRunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = draft_fixtures.CapitalResearchDraftTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

        root = research_store.STATE_DIRECTORY / "tasks"
        for name, value in (
            ("STATE_DIRECTORY", root),
            ("TASK_STATE_FILE", root / "tasks.json"),
            ("TASK_LOCK_FILE", root / "tasks.lock"),
        ):
            mocked = patch.object(tasks, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)

        mocked = patch(
            "app.agents.capital_runner.read_operating_policy",
            return_value=CapitalOperatingPolicy(),
        )
        self.policy = mocked.start()
        self.addCleanup(mocked.stop)

    def create(self, **changes):
        instruction = {
            "action": "capital.revise_research",
            "parent_research_id": self.fixture.parent.research_id,
            "objective": "Investigate the failed attempt.",
        }
        instruction.update(changes)
        return tasks.create_task(
            title="Research revision",
            objective=json.dumps(instruction),
            assigned_agent_id=RESEARCH_AGENT_ID,
        )

    def current(self, task):
        with tasks._state_lock():
            state = tasks._load_state_unlocked()
            return tasks._task_from_record(state["tasks"][task.task_id])

    def test_complete_revision_workflow(self):
        task = self.create()
        completed = capital_runner.run_research_task(task)
        self.assertEqual(completed.status, tasks.TaskStatus.COMPLETED)
        result = json.loads(completed.result)
        self.assertEqual(result["status"], "revision_created")
        self.assertEqual(result["candidate"]["status"], "proposed")
        self.assertFalse(result["promotion_authorized"])
        self.fixture.model.assert_called_once()
        self.assertEqual(len(self.fixture.read()["candidates"]), 2)

    def test_completed_task_cannot_run_again(self):
        task = self.create()
        completed = capital_runner.run_research_task(task)
        with self.assertRaises(ValueError):
            capital_runner.run_research_task(completed)
        with self.assertRaises(ValueError):
            capital_runner.run_research_task(task)
        self.fixture.model.assert_called_once()

    def test_pause_leaves_task_queued(self):
        task = self.create()
        self.policy.return_value = CapitalOperatingPolicy(paused=True)
        with self.assertRaises(CapitalPolicyError):
            capital_runner.run_research_task(task)
        self.assertEqual(self.current(task).status, tasks.TaskStatus.QUEUED)
        self.fixture.model.assert_not_called()

    def test_unknown_action_is_not_claimed(self):
        task = self.create(action="capital.promote_paper")
        with self.assertRaises(ValueError):
            capital_runner.run_research_task(task)
        self.assertEqual(self.current(task).status, tasks.TaskStatus.QUEUED)

    def test_model_failure_records_failed_task(self):
        task = self.create()
        self.fixture.model.side_effect = RuntimeError("Model unavailable")
        with self.assertRaises(RuntimeError):
            capital_runner.run_research_task(task)
        self.assertEqual(self.current(task).status, tasks.TaskStatus.FAILED)
        self.assertEqual(len(self.fixture.read()["candidates"]), 1)

    def test_lost_ownership_prevents_candidate_creation(self):
        task = self.create()

        def recover_during_generation(**kwargs):
            with tasks._state_lock():
                state = tasks._load_state_unlocked()
                row = state["tasks"][task.task_id]
                row["status"] = "queued"
                row["execution_attempts"] += 1
                tasks._save_state_unlocked(state)
            return self.fixture.proposal.copy()

        self.fixture.model.side_effect = recover_during_generation
        with self.assertRaises(ValueError):
            capital_runner.run_research_task(task)
        self.assertEqual(self.current(task).status, tasks.TaskStatus.QUEUED)
        self.assertEqual(len(self.fixture.read()["candidates"]), 1)

    def test_retry_after_completion_failure_reuses_revision(self):
        task = self.create()
        with patch(
            "app.agents.capital_runner.tasks.complete_task",
            side_effect=RuntimeError("Completion write failed"),
        ):
            with self.assertRaises(RuntimeError):
                capital_runner.run_research_task(task)

        self.assertEqual(len(self.fixture.read()["candidates"]), 2)

        # Simulate scheduling a retry of the same task identity.
        with tasks._state_lock():
            state = tasks._load_state_unlocked()
            state["tasks"][task.task_id]["status"] = "queued"
            tasks._save_state_unlocked(state)

        completed = capital_runner.run_research_task(self.current(task))
        self.assertEqual(completed.status, tasks.TaskStatus.COMPLETED)
        self.assertEqual(completed.execution_attempts, 2)
        self.assertEqual(len(self.fixture.read()["candidates"]), 2)
        self.fixture.model.assert_called_once()


if __name__ == "__main__":
    unittest.main()
