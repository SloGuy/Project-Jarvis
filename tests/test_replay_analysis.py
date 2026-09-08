import copy
from decimal import Decimal as D
import runpy
from pathlib import Path
import unittest

from app.capital.replay_analysis import analyze_report, drawdown

fixture = runpy.run_path(str(
    Path(__file__).with_name("test_offline_verification.py")
))["fixture"]


class AnalysisTests(unittest.TestCase):
    def test_drawdown_includes_initial_capital(self):
        self.assertEqual(
            drawdown([{"equity": "90"}, {"equity": "95"}], D("100")),
            D("10"),
        )

    def test_accounting_curve_and_benchmark(self):
        report = fixture()
        before = copy.deepcopy(report)
        result = analyze_report(report)["scenarios"]["costs"]
        self.assertEqual(report, before)
        self.assertEqual(len(result["equity_curve"]), 16)
        self.assertEqual(result["completed_trades"], 1)
        self.assertEqual(
            result["ending_equity"],
            report["scenarios"]["costs"]["account"]["equity"],
        )
        self.assertEqual(result["exit_reasons"], {"fixed_mean_recovery": 1})
        self.assertIsNotNone(result["benchmark"]["entry_at"])
        self.assertGreater(D(result["benchmark"]["ending_equity"]), D("1000"))

    def test_corrupt_expected_result_rejected(self):
        report = fixture()
        report["scenarios"]["costs"]["account"]["equity"] = "99999"
        with self.assertRaises(ValueError):
            analyze_report(report)


if __name__ == "__main__":
    unittest.main()
