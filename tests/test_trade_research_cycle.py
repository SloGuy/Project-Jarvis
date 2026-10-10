import unittest
from unittest.mock import patch

from app.capital import trade_research_cycle as cycle


class TradeResearchCycleTests(unittest.TestCase):
    def setUp(self):
        self.experiments = [
            {
                "experiment_id": "mr2",
                "status": "running",
                "execution_mode": "autonomous_paper_trading",
            },
            {
                "experiment_id": "momentum",
                "status": "running",
                "execution_mode": "autonomous_paper_trading",
            },
        ]
        self.mocks = {}
        for name, value in (
            ("_authorize", None),
            ("read_experiments", self.experiments),
            ("queue_trade_research", {
                "status": "queued",
                "scheduled_count": 1,
                "request": {
                    "request_id": "synthetic-request",
                    "status": "queued",
                },
            }),
            ("process_trade_research_once", {
                "status": "advisory_proposal_saved",
                "processed_count": 1,
            }),
        ):
            patcher = patch.object(cycle, name, return_value=value)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def run_cycle(self):
        return cycle.run_trade_research_cycle()

    def test_running_paper_experiments_are_scheduled_in_order(self):
        result = self.run_cycle()
        self.assertEqual(result["eligible_experiment_count"], 2)
        self.assertEqual(result["scheduled_count"], 2)
        self.assertEqual(result["diagnostic_failure_count"], 0)
        self.assertEqual(
            [
                call.kwargs["experiment_id"]
                for call in self.mocks["queue_trade_research"].call_args_list
            ],
            ["momentum", "mr2"],
        )
        self.mocks["process_trade_research_once"].assert_called_once()

    def test_nonrunning_and_nonpaper_experiments_are_skipped(self):
        self.experiments.extend([
            {
                "experiment_id": "paused",
                "status": "paused",
                "execution_mode": "autonomous_paper_trading",
            },
            {
                "experiment_id": "completed",
                "status": "completed",
                "execution_mode": "autonomous_paper_trading",
            },
            {
                "experiment_id": "live",
                "status": "running",
                "execution_mode": "live",
            },
        ])
        result = self.run_cycle()
        self.assertEqual(result["eligible_experiment_count"], 2)
        self.assertEqual(
            self.mocks["queue_trade_research"].call_count, 2
        )

    def test_existing_requests_do_not_increase_scheduled_count(self):
        self.mocks["queue_trade_research"].return_value = {
            "status": "already_requested",
            "scheduled_count": 0,
            "request": {
                "request_id": "existing",
                "status": "completed",
            },
        }
        result = self.run_cycle()
        self.assertEqual(result["scheduled_count"], 0)
        self.assertEqual(
            result["scheduling"][0]["request_status"], "completed"
        )

    def test_diagnostic_failure_is_visible_and_other_work_continues(self):
        self.mocks["queue_trade_research"].side_effect = [
            ValueError("sensitive database URL"),
            {
                "status": "waiting_for_sample",
                "scheduled_count": 0,
            },
        ]
        result = self.run_cycle()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["diagnostic_failure_count"], 1)
        self.assertEqual(
            result["scheduling"][0]["error_type"], "ValueError"
        )
        self.assertNotIn("sensitive database URL", str(result))
        self.assertEqual(
            result["scheduling"][1]["status"], "waiting_for_sample"
        )
        self.mocks["process_trade_research_once"].assert_called_once()

    def test_permission_failure_stops_scheduling_and_processing(self):
        self.mocks["queue_trade_research"].side_effect = PermissionError(
            "paused"
        )
        with self.assertRaises(PermissionError):
            self.run_cycle()
        self.assertEqual(self.mocks["queue_trade_research"].call_count, 1)
        self.mocks["process_trade_research_once"].assert_not_called()

    def test_initial_authority_denial_prevents_inventory_read(self):
        self.mocks["_authorize"].side_effect = PermissionError("disabled")
        with self.assertRaises(PermissionError):
            self.run_cycle()
        self.mocks["read_experiments"].assert_not_called()
        self.mocks["process_trade_research_once"].assert_not_called()

    def test_authority_is_rechecked_before_processing(self):
        self.mocks["_authorize"].side_effect = [
            None, None, None, PermissionError("paused"),
        ]
        with self.assertRaises(PermissionError):
            self.run_cycle()
        self.assertEqual(self.mocks["queue_trade_research"].call_count, 2)
        self.mocks["process_trade_research_once"].assert_not_called()

    def test_duplicate_experiments_are_rejected(self):
        self.experiments.append(dict(self.experiments[0]))
        with self.assertRaisesRegex(ValueError, "Duplicate experiment"):
            self.run_cycle()
        self.mocks["queue_trade_research"].assert_not_called()

    def test_invalid_inventory_is_rejected(self):
        self.mocks["read_experiments"].return_value = None
        with self.assertRaisesRegex(ValueError, "inventory"):
            self.run_cycle()

    def test_missing_experiment_identity_is_rejected(self):
        self.experiments[0]["experiment_id"] = ""
        with self.assertRaisesRegex(ValueError, "identity"):
            self.run_cycle()
        self.mocks["queue_trade_research"].assert_not_called()

    def test_empty_inventory_can_process_existing_queue(self):
        self.mocks["read_experiments"].return_value = []
        result = self.run_cycle()
        self.assertEqual(result["eligible_experiment_count"], 0)
        self.mocks["queue_trade_research"].assert_not_called()
        self.mocks["process_trade_research_once"].assert_called_once()

    def test_model_processing_failure_propagates(self):
        self.mocks["process_trade_research_once"].side_effect = RuntimeError(
            "model failed"
        )
        with self.assertRaises(RuntimeError):
            self.run_cycle()

    def test_result_grants_no_authority(self):
        result = self.run_cycle()
        for field in (
            "research_candidate_changed",
            "strategy_change_authorized",
            "promotion_authorized",
            "live_capital_authorized",
        ):
            self.assertIs(result[field], False)


if __name__ == "__main__":
    unittest.main()
