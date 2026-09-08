from copy import deepcopy
import runpy
import unittest
from unittest.mock import patch

from app.capital.validation_plan import seal_plan, plan_digest
from app.capital.validation_assessment import assess_metrics, assess_report

fixture = runpy.run_path("tests/test_validation_plan.py")


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.plan = seal_plan(fixture["draft"](), now=fixture["NOW"])["plan"]
        self.summary = {
            "completed_trades": 10,
            "return_percent": "1",
            "maximum_marked_drawdown_percent": "2",
            "benchmark": {"return_percent": "0.5", "entry_at": self.plan["start"]},
            "data_quality": {
                "regular_ticks": 20,
                "missing_or_stale_reference_ticks": 0,
                "unusable_regular_ticks": 0,
            },
        }

    def assess(self, available=True):
        return assess_metrics(
            self.plan, self.summary, decision_ticks=100,
            availability_verified=available,
        )

    def test_numeric_pass_never_authorizes_promotion(self):
        result = self.assess()
        self.assertEqual(result["criteria_status"], "pass")
        self.assertEqual(result["validation_status"], "pass")
        self.assertFalse(result["promotion_authorized"])

    def test_unverified_availability_prevents_validation_pass(self):
        result = self.assess(available=False)
        self.assertEqual(result["criteria_status"], "pass")
        self.assertEqual(result["validation_status"], "insufficient_evidence")

    def test_performance_failure(self):
        self.summary["return_percent"] = "-1"
        result = self.assess()
        self.assertEqual(result["validation_status"], "fail")
        self.assertIn("minimum_return_percent", result["failed_performance_criteria"])

    def test_sample_and_quality_insufficiency(self):
        for key, value in (
            ("completed_trades", 9),
            ("data_quality", {"regular_ticks": 20, "missing_or_stale_reference_ticks": 6}),
            ("data_quality", {"regular_ticks": 20, "unusable_regular_ticks": 3}),
        ):
            old = deepcopy(self.summary[key])
            self.summary[key] = value
            self.assertEqual(self.assess()["validation_status"], "insufficient_evidence")
            self.summary[key] = old

    def test_threshold_equality_is_inclusive(self):
        self.summary["return_percent"] = "0"
        self.summary["benchmark"]["return_percent"] = "0"
        self.summary["maximum_marked_drawdown_percent"] = "5"
        self.summary["data_quality"].update({
            "missing_or_stale_reference_ticks": 5,
            "unusable_regular_ticks": 2,
        })
        self.assertEqual(self.assess()["validation_status"], "pass")

    def test_invalid_counts_and_nonfinite_values(self):
        self.summary["completed_trades"] = True
        with self.assertRaises(ValueError):
            self.assess()
        self.summary["completed_trades"] = 10
        self.summary["return_percent"] = "NaN"
        with self.assertRaises(ValueError):
            self.assess()

    def test_report_binding_checked_before_analysis(self):
        report = {
            key: self.plan[key] for key in (
                "asset_id", "symbol", "provider", "start", "end_exclusive",
                "policy", "execution_manifest",
            )
        }
        report.update({
            "designation": "development",
            "validation_registration": {"sha256": plan_digest(self.plan)},
        })
        with patch("app.capital.validation_assessment.analyze_report") as analyze:
            with self.assertRaises(ValueError):
                assess_report(self.plan, report)
            report["designation"] = "prospective_validation"
            report["validation_registration"]["sha256"] = "changed"
            with self.assertRaises(ValueError):
                assess_report(self.plan, report)
            analyze.assert_not_called()


if __name__ == "__main__":
    unittest.main()
