import fcntl
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.agents import capital_worker as worker
from app.agents.capital_registry import RESEARCH_AGENT_ID
from app.agents.tasks import TaskStatus


class CapitalWorkerTradeResearchTests(unittest.TestCase):
    def setUp(self):
        self.policy = SimpleNamespace(enabled=True, paused=False)
        self.scheduling = {
            "status": "no_new_revision_work",
            "scheduled_count": 0,
        }
        self.trade = {
            "diagnostic_failure_count": 0,
            "processing": {
                "status": "advisory_proposal_saved",
                "processed_count": 1,
            },
        }
        self.mocks = {}
        for name, value in (
            ("read_operating_policy", self.policy),
            ("recover_capital_research_tasks", {}),
            ("schedule_research_revision", self.scheduling),
            ("get_tasks", []),
            ("run_research_task", None),
            ("run_trade_research_cycle", self.trade),
        ):
            patcher = patch.object(worker, name, return_value=value)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def task(self, identifier, created_at, agent=RESEARCH_AGENT_ID):
        return SimpleNamespace(
            task_id=identifier,
            created_at=created_at,
            assigned_agent_id=agent,
            status=TaskStatus.QUEUED,
        )

    def test_queued_revision_has_priority(self):
        task = self.task("revision-1", "2026-10-01")
        self.mocks["get_tasks"].return_value = [task]
        self.mocks["run_research_task"].return_value = SimpleNamespace(
            task_id="revision-1",
            status=TaskStatus.COMPLETED,
            execution_attempts=1,
        )
        result = worker._cycle()
        self.assertEqual(result["task_id"], "revision-1")
        self.assertEqual(result["processed_count"], 1)
        self.mocks["run_research_task"].assert_called_once_with(task)
        self.mocks["run_trade_research_cycle"].assert_not_called()

    def test_oldest_revision_is_selected(self):
        older = self.task("older", "2026-10-01")
        newer = self.task("newer", "2026-10-02")
        self.mocks["get_tasks"].return_value = [newer, older]
        self.mocks["run_research_task"].return_value = SimpleNamespace(
            task_id="older",
            status=TaskStatus.COMPLETED,
            execution_attempts=2,
        )
        worker._cycle()
        self.mocks["run_research_task"].assert_called_once_with(older)

    def test_pending_running_revision_blocks_diagnostic_generation(self):
        self.scheduling["status"] = "work_pending"
        result = worker._cycle()
        self.assertEqual(result["reason"], "revision_work_pending")
        self.assertEqual(result["processed_count"], 0)
        self.mocks["run_trade_research_cycle"].assert_not_called()

    def test_idle_revision_queue_runs_trade_research(self):
        result = worker._cycle()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["processed_count"], 1)
        self.assertEqual(
            result["scope"], "trade_diagnosis_and_advisory_research"
        )
        self.mocks["run_trade_research_cycle"].assert_called_once()

    def test_other_agents_tasks_do_not_enter_revision_runner(self):
        self.mocks["get_tasks"].return_value = [
            self.task("engineering", "2026-10-01", agent="engineering")
        ]
        worker._cycle()
        self.mocks["run_research_task"].assert_not_called()
        self.mocks["run_trade_research_cycle"].assert_called_once()

    def test_disabled_worker_performs_no_work(self):
        self.policy.enabled = False
        result = worker._cycle()
        self.assertEqual(result["status"], "disabled")
        self.mocks["recover_capital_research_tasks"].assert_not_called()
        self.mocks["schedule_research_revision"].assert_not_called()
        self.mocks["run_trade_research_cycle"].assert_not_called()

    def test_paused_worker_performs_no_new_research(self):
        self.policy.paused = True
        result = worker._cycle()
        self.assertEqual(result["status"], "paused")
        self.mocks["schedule_research_revision"].assert_not_called()
        self.mocks["run_trade_research_cycle"].assert_not_called()

    def test_partial_diagnostics_remain_visible(self):
        self.trade["diagnostic_failure_count"] = 1
        self.trade["processing"] = {"status": "idle", "processed_count": 0}
        result = worker._cycle()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(
            result["trade_research"]["diagnostic_failure_count"], 1
        )

    def test_blocked_trade_request_is_reported(self):
        self.trade["processing"] = {
            "status": "blocked_running_request",
            "processed_count": 0,
        }
        result = worker._cycle()
        self.assertEqual(result["status"], "blocked_running_request")

    def test_model_failure_propagates(self):
        self.mocks["run_trade_research_cycle"].side_effect = RuntimeError(
            "model failure"
        )
        with self.assertRaises(RuntimeError):
            worker._cycle()

    def test_run_once_reports_scope_and_denies_live_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            result = worker.run_once(directory=directory)
        self.assertEqual(
            result["scope"], "trade_diagnosis_and_advisory_research"
        )
        self.assertFalse(result["live_capital_authorized"])
        self.assertIn("started_at", result)
        self.assertIn("finished_at", result)

    def test_worker_lock_prevents_parallel_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "worker.lock"
            with path.open("a+") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = worker.run_once(directory=directory)
        self.assertEqual(result["status"], "busy")
        self.mocks["read_operating_policy"].assert_not_called()
        self.mocks["run_trade_research_cycle"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
