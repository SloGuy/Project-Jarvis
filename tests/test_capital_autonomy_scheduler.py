"""Scheduler tests using isolated task and research stores."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import test_capital_autonomy_runner as runner_fixtures

from app.agents import tasks
from app.agents.capital_registry import RESEARCH_AGENT_ID
from app.agents.capital_scheduler import schedule_research_revision
from app.capital import research_store
from app.capital.autonomy_policy import CapitalOperatingPolicy


class CapitalSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.environment = runner_fixtures.CapitalRunnerTests()
        self.addCleanup(self.environment.doCleanups)
        self.environment.setUp()

        mocked = patch(
            "app.agents.capital_scheduler.read_operating_policy",
            return_value=CapitalOperatingPolicy(),
        )
        self.policy = mocked.start()
        self.addCleanup(mocked.stop)

    def test_revision_is_queued_with_explicit_instruction(self):
        result = schedule_research_revision()
        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(result["scheduled_count"], 1)

        queued = tasks.get_tasks()
        self.assertEqual(len(queued), 1)
        task = queued[0]
        self.assertEqual(task.assigned_agent_id, RESEARCH_AGENT_ID)
        self.assertEqual(task.status, tasks.TaskStatus.QUEUED)
        instruction = json.loads(task.objective)
        self.assertEqual(instruction["action"], "capital.revise_research")
        self.assertEqual(
            instruction["parent_research_id"], "research_parent"
        )
        self.environment.fixture.model.assert_not_called()

    def test_repeated_cycle_does_not_duplicate_work(self):
        first = schedule_research_revision()
        second = schedule_research_revision()
        self.assertEqual(second["status"], "work_pending")
        self.assertEqual(second["task_ids"], [first["task_id"]])
        self.assertEqual(len(tasks.get_tasks()), 1)

    def test_running_task_prevents_new_work(self):
        result = schedule_research_revision()
        tasks.claim_task(
            task_id=result["task_id"],
            agent_id=RESEARCH_AGENT_ID,
        )
        self.assertEqual(
            schedule_research_revision()["status"], "work_pending"
        )
        self.assertEqual(len(tasks.get_tasks()), 1)

    def test_disabled_scheduler_creates_no_task(self):
        self.policy.return_value = CapitalOperatingPolicy(enabled=False)
        self.assertEqual(
            schedule_research_revision()["status"], "disabled"
        )
        self.assertEqual(len(tasks.get_tasks()), 0)

    def test_paused_scheduler_creates_no_task(self):
        self.policy.return_value = CapitalOperatingPolicy(paused=True)
        self.assertEqual(
            schedule_research_revision()["status"], "paused"
        )
        self.assertEqual(len(tasks.get_tasks()), 0)

    def test_rejected_research_is_not_reopened(self):
        with research_store.locked_research_state(write=True) as state:
            state["candidates"]["research_parent"]["status"] = "rejected"
        result = schedule_research_revision()
        self.assertEqual(result["status"], "no_new_revision_work")
        self.assertEqual(len(tasks.get_tasks()), 0)

    def test_completed_request_is_not_replaced(self):
        result = schedule_research_revision()
        task = tasks.claim_task(
            task_id=result["task_id"],
            agent_id=RESEARCH_AGENT_ID,
        )
        tasks.complete_task(
            task_id=task.task_id,
            result="Existing result",
            expected_execution_attempt=task.execution_attempts,
        )
        result = schedule_research_revision()
        self.assertEqual(result["status"], "no_new_revision_work")
        self.assertEqual(len(tasks.get_tasks()), 1)
        self.assertEqual(
            result["previous_requests"][0]["status"], "completed"
        )

    def test_failed_request_does_not_block_other_research(self):
        first = schedule_research_revision()
        task = tasks.claim_task(
            task_id=first["task_id"],
            agent_id=RESEARCH_AGENT_ID,
        )
        tasks.fail_task(
            task_id=task.task_id,
            error="Test failure",
            expected_execution_attempt=task.execution_attempts,
        )

        with research_store.locked_research_state(write=True) as state:
            other = deepcopy(state["candidates"]["research_parent"])
            other["research_id"] = "research_second"
            other["strategy_name"] = "another_strategy"
            state["candidates"]["research_second"] = other

        result = schedule_research_revision()
        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(result["parent_research_id"], "research_second")
        self.assertEqual(len(tasks.get_tasks()), 2)

    def test_scheduler_preserves_research_records(self):
        before = self.environment.fixture.read()
        schedule_research_revision()
        self.assertEqual(self.environment.fixture.read(), before)


if __name__ == "__main__":
    unittest.main()
