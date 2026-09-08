from copy import deepcopy
from datetime import timedelta
import runpy
import unittest

from app.capital.paper_comparison import compare_activity, at

fixture = runpy.run_path("tests/test_offline_verification.py")["fixture"]


class PaperComparisonTests(unittest.TestCase):
    def setUp(self):
        self.report = fixture()
        self.report.update(asset_id=1, symbol="SPY")
        self.paper = {
            "portfolio_id": 1, "strategy_name": "mean_reversion_v2",
            "asset_id": 1, "symbol": "SPY",
            "start": self.report["start"],
            "end_exclusive": self.report["end_exclusive"],
            "journals": [],
        }

    def test_flat_period_and_explicit_count_difference(self):
        result = compare_activity(self.report, self.paper)
        self.assertEqual(result["initial_position_alignment"], "both_flat_for_asset")
        self.assertEqual(result["scenarios"]["costs"]["entry_count_difference"], 1)
        self.assertFalse(result["portfolio_return_comparable"])
        self.assertFalse(result["promotion_authorized"])

    def test_carry_in_and_end_boundary(self):
        start, end = at(self.paper["start"]), at(self.paper["end_exclusive"])
        self.paper["journals"] = [
            {"id": 1, "opened_at": (start - timedelta(days=1)).isoformat(),
             "closed_at": start.isoformat()},
            {"id": 2, "opened_at": start.isoformat(), "closed_at": end.isoformat()},
        ]
        result = compare_activity(self.report, self.paper)
        self.assertEqual(result["paper_carry_in_journal_ids"], [1])
        self.assertEqual(result["paper_carry_out_journal_ids"], [2])
        self.assertEqual(len(result["paper_entries"]), 1)
        self.assertEqual(len(result["paper_exits"]), 1)
        self.assertEqual(result["paper_completed_within_window"], 0)

    def test_identity_and_duplicate_rejected(self):
        wrong = deepcopy(self.paper)
        wrong["asset_id"] = 2
        with self.assertRaises(ValueError):
            compare_activity(self.report, wrong)
        row = {"id": 1, "opened_at": self.paper["start"], "closed_at": None}
        self.paper["journals"] = [row, row]
        with self.assertRaises(ValueError):
            compare_activity(self.report, self.paper)

    def test_tampered_replay_rejected(self):
        self.report["scenarios"]["costs"]["account"]["cash"] = "999999"
        with self.assertRaises(ValueError):
            compare_activity(self.report, self.paper)


if __name__ == "__main__":
    unittest.main()
