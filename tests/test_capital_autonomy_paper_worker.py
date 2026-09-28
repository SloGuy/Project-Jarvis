"""Worker scheduling tests without database access or trade execution."""

import fcntl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.capital import autonomy_paper_worker as worker


class AutonomousPaperWorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

        self.session = MagicMock()
        self.session.scalars.return_value = [10, 20]
        factory = MagicMock()
        factory.return_value.__enter__.return_value = self.session
        self.patch("SessionLocal", factory)
        self.runner = self.patch(
            "run_mean_reversion_v2_paper_cycle",
            return_value={
                "status": "success",
                "executed_count": 0,
                "execution_failed_count": 0,
                "results": [],
            },
        )

    def patch(self, name, *args, **kwargs):
        patcher = patch.object(worker, name, *args, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def run_cycle(self):
        return worker.run_paper_cycle(directory=self.directory)

    def test_each_selected_portfolio_gets_full_exit_evaluation(self):
        result = self.run_cycle()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.runner.call_count, 2)
        self.assertEqual(
            [call.kwargs for call in self.runner.call_args_list],
            [
                {"portfolio_id": 10, "risk_only": False},
                {"portfolio_id": 20, "risk_only": False},
            ],
        )
        self.assertFalse(result["live_capital_authorized"])

    def test_no_portfolios_is_idle(self):
        self.session.scalars.return_value = []
        self.assertEqual(self.run_cycle()["status"], "idle")
        self.runner.assert_not_called()

    def test_overlap_returns_busy_without_database_or_runner(self):
        with (self.directory / "paper-worker.lock").open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_cycle()
        self.assertEqual(result["status"], "busy")
        self.session.scalars.assert_not_called()
        self.runner.assert_not_called()

    def test_one_exception_does_not_skip_other_portfolios(self):
        self.runner.side_effect = [
            RuntimeError("Unavailable portfolio."),
            {
                "status": "success",
                "executed_count": 1,
                "execution_failed_count": 0,
                "results": [],
            },
        ]
        result = self.run_cycle()
        self.assertEqual(result["status"], "partial_failure")
        self.assertEqual(self.runner.call_count, 2)
        first, second = result["portfolios"]
        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["error_type"], "RuntimeError")
        self.assertEqual(second["status"], "evaluated")
        self.assertEqual(second["executed_count"], 1)

    def test_reported_execution_failure_is_not_reported_as_success(self):
        self.runner.return_value = {
            "status": "success",
            "executed_count": 0,
            "execution_failed_count": 1,
            "results": [],
        }
        result = self.run_cycle()
        self.assertEqual(result["status"], "partial_failure")
        self.assertTrue(all(
            row["status"] == "execution_failed"
            for row in result["portfolios"]
        ))

    def test_lock_releases_after_database_failure(self):
        self.session.scalars.side_effect = RuntimeError("Database unavailable.")
        with self.assertRaises(RuntimeError):
            self.run_cycle()
        self.session.scalars.side_effect = None
        self.session.scalars.return_value = []
        self.assertEqual(self.run_cycle()["status"], "idle")

    def test_sequential_cycles_can_both_run(self):
        self.assertEqual(self.run_cycle()["status"], "completed")
        self.assertEqual(self.run_cycle()["status"], "completed")
        self.assertEqual(self.runner.call_count, 4)


if __name__ == "__main__":
    unittest.main()
