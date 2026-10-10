import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.capital import autonomy_trade_research as queue
from app.capital import research_store
from app.capital.trade_diagnosis import build_trade_diagnosis


class TradeResearchQueueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)

        for name, value in (
            ("STATE_DIRECTORY", root),
            ("RESEARCH_STATE_FILE", root / "research.json"),
            ("RESEARCH_LOCK_FILE", root / "research.lock"),
        ):
            patcher = patch.object(research_store, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        with research_store.locked_research_state(write=True) as state:
            state["candidates"]["existing-btc"] = {
                "hypothesis": "Existing registered BTC hypothesis",
                "status": "proposed",
                "validation_assessments": [],
            }
        self.candidates_before = self.state()["candidates"]

        configuration = {
            "experiment": {"experiment_id": "mr2-paper"},
            "portfolio": {"id": 5},
            "risk_policy": {"name": "mr2-policy"},
            "committee_thresholds": {"minimum_profit_factor": "1.20"},
        }
        rows = [
            {
                "id": identifier,
                "symbol": "BTC",
                "status": "closed",
                "strategy_name": "mean_reversion_v2",
                "exit_rule": rule,
                "realized_gain_loss_usd": pnl,
                "return_percent": percent,
            }
            for identifier, rule, pnl, percent in (
                (1, "fixed_mean_recovery", "1", "1"),
                (2, "stop_loss", "-2", "-6"),
            )
        ]
        self.diagnosis = build_trade_diagnosis(
            experiment_id="mr2-paper",
            strategy_name="mean_reversion_v2",
            portfolio_id=5,
            journals=rows,
            queried_at="2026-10-08T23:00:00+00:00",
            minimum_profit_factor="1.20",
            stop_loss_percent="5",
            minimum_closed_trades=2,
        )
        self.diagnosis.update({
            "configuration": configuration,
            "configuration_sha256": queue._digest(configuration),
            "configuration_rechecked": True,
        })

        self.authority = self.mock("_authorize", None)
        self.collector = self.mock("collect_diagnosis", self.diagnosis)
        self.current = self.mock("current_configuration", configuration)

    def mock(self, name, value):
        patcher = patch.object(queue, name, return_value=value)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def state(self):
        with research_store.locked_research_state() as state:
            return copy.deepcopy(state)

    def run_queue(self):
        return queue.queue_trade_research(experiment_id="mr2-paper")

    def test_mature_failure_is_persisted(self):
        result = self.run_queue()
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["scheduled_count"], 1)
        request = result["request"]
        saved = self.state()[queue.STORE_KEY][request["request_id"]]
        self.assertEqual(saved, request)
        self.assertEqual(saved["status"], "queued")
        self.assertEqual(
            saved["diagnosis_sha256"], queue._digest(self.diagnosis)
        )
        self.collector.assert_called_once_with("mr2-paper")

    def test_repeat_returns_original_request(self):
        first = self.run_queue()
        changed = copy.deepcopy(self.diagnosis)
        changed["queried_at"] = "2026-10-09T00:00:00+00:00"
        self.collector.return_value = changed
        second = self.run_queue()
        self.assertEqual(second["status"], "already_requested")
        self.assertEqual(second["scheduled_count"], 0)
        self.assertEqual(first["request"], second["request"])
        self.assertEqual(len(self.state()[queue.STORE_KEY]), 1)

    def test_existing_candidates_are_unchanged(self):
        self.run_queue()
        self.assertEqual(
            self.state()["candidates"], self.candidates_before
        )

    def test_truncated_history_does_not_queue(self):
        self.diagnosis["query_limit_reached"] = True
        self.assertEqual(
            self.run_queue()["status"], "blocked_incomplete_history"
        )
        self.assertNotIn(queue.STORE_KEY, self.state())

    def test_small_sample_does_not_queue(self):
        self.diagnosis["thresholds"]["minimum_closed_trades"] = 100
        self.assertEqual(self.run_queue()["status"], "waiting_for_sample")
        self.assertNotIn(queue.STORE_KEY, self.state())

    def test_passing_or_undefined_profit_factor_does_not_queue(self):
        for factor in ("1.20", "1.50", None):
            with self.subTest(factor=factor):
                self.diagnosis["summary"]["profit_factor"] = factor
                self.assertEqual(
                    self.run_queue()["status"], "no_profit_factor_failure"
                )
                self.assertNotIn(queue.STORE_KEY, self.state())

    def test_denied_authority_prevents_collection(self):
        self.authority.side_effect = PermissionError("disabled")
        with self.assertRaises(PermissionError):
            self.run_queue()
        self.collector.assert_not_called()
        self.assertNotIn(queue.STORE_KEY, self.state())

    def test_authority_denied_before_write_preserves_state(self):
        before = research_store.RESEARCH_STATE_FILE.read_bytes()
        self.authority.side_effect = [
            None, None, PermissionError("paused"),
        ]
        with self.assertRaises(PermissionError):
            self.run_queue()
        self.assertEqual(
            research_store.RESEARCH_STATE_FILE.read_bytes(), before
        )

    def test_changed_configuration_prevents_persistence(self):
        self.current.return_value = {"changed": True}
        with self.assertRaisesRegex(ValueError, "Configuration changed"):
            self.run_queue()
        self.assertNotIn(queue.STORE_KEY, self.state())

    def test_changed_configuration_hash_is_rejected(self):
        self.diagnosis["configuration"]["portfolio"]["id"] = 1
        with self.assertRaisesRegex(ValueError, "configuration hash"):
            self.run_queue()

    def test_wrong_experiment_is_rejected(self):
        self.diagnosis["experiment_id"] = "another-experiment"
        with self.assertRaisesRegex(ValueError, "experiment mismatch"):
            self.run_queue()

    def test_unexpected_authority_is_rejected(self):
        self.diagnosis["live_capital_authorized"] = True
        with self.assertRaisesRegex(ValueError, "scope or authority"):
            self.run_queue()

    def test_corrupt_saved_diagnosis_is_not_overwritten(self):
        request_id = self.run_queue()["request"]["request_id"]
        with research_store.locked_research_state(write=True) as state:
            state[queue.STORE_KEY][request_id]["diagnosis"]["findings"] = [
                "tampered",
            ]
        before = research_store.RESEARCH_STATE_FILE.read_bytes()
        with self.assertRaisesRegex(ValueError, "diagnosis hash"):
            self.run_queue()
        self.assertEqual(
            research_store.RESEARCH_STATE_FILE.read_bytes(), before
        )

    def test_failed_request_is_not_replaced_with_fresh_work(self):
        request_id = self.run_queue()["request"]["request_id"]
        with research_store.locked_research_state(write=True) as state:
            state[queue.STORE_KEY][request_id]["status"] = "failed"
        result = self.run_queue()
        self.assertEqual(result["status"], "already_requested")
        self.assertEqual(result["request"]["status"], "failed")
        self.assertEqual(len(self.state()[queue.STORE_KEY]), 1)

    def test_returned_request_cannot_mutate_saved_state(self):
        result = self.run_queue()
        result["request"]["diagnosis"]["findings"].append("changed")
        saved = self.state()[queue.STORE_KEY][
            result["request"]["request_id"]
        ]
        self.assertNotIn("changed", saved["diagnosis"]["findings"])

    def test_request_grants_no_authority(self):
        request = self.run_queue()["request"]
        for field in (
            "research_candidate_changed",
            "validation_verified",
            "strategy_change_authorized",
            "promotion_authorized",
            "live_capital_authorized",
        ):
            self.assertIs(request[field], False)


if __name__ == "__main__":
    unittest.main()
