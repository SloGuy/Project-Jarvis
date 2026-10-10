"""Comparison assessment rules and verified-packet integration."""

import unittest
from copy import deepcopy
from decimal import Decimal as D

import test_validation_provider_integration as provider_fixture
import test_cooldown_analysis as analysis_fixture

from app.capital.cooldown_assessment import (
    assess_comparison_metrics, assess_cooldown_packet,
)
from test_position_simulation import POLICY


class CooldownAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.fixture = provider_fixture.ProviderIntegrationTests(
            methodName="test_replay_and_assessment_complete_without_database"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.plan = deepcopy(self.fixture.plan)
        self.minimum = max(
            100, self.plan["criteria"]["minimum_completed_trades"]
        )
        passing_return = max(
            D(self.plan["criteria"]["minimum_return_percent"]),
            D(self.plan["criteria"]["minimum_excess_return_percent"]),
            D("0"),
        ) + 1

        summary = {
            "has_equity_marks": True,
            "completed_trades": self.minimum,
            "return_percent": str(passing_return),
            "maximum_marked_drawdown_percent": "0",
            "data_quality": {
                "regular_ticks": 20,
                "missing_or_stale_reference_ticks": 0,
                "unusable_regular_ticks": 0,
            },
            "benchmark": {
                "entry_at": self.plan["start"],
                "return_percent": "0",
            },
            "profit_factor": "1.1",
            "profit_factor_status": "defined",
        }
        self.accounts = {
            "baseline": deepcopy(summary),
            "intervention": deepcopy(summary),
        }
        self.accounts["intervention"]["profit_factor"] = "1.3"

    def assess(self, **changes):
        arguments = {
            "decision_ticks": 100,
            "availability_verified": True,
            "minimum_completed_trades": self.minimum,
            "minimum_profit_factor": "1.2",
        }
        arguments.update(changes)
        return assess_comparison_metrics(
            self.plan, self.accounts, **arguments
        )

    def test_passing_metrics_do_not_authorize_validation_or_promotion(self):
        result = self.assess()
        self.assertEqual(result["comparison_criteria_status"], "pass")
        self.assertEqual(result["validation_status"], "insufficient_evidence")
        for field in (
            "comparison_preregistered",
            "validation_ready",
            "strategy_change_authorized",
            "promotion_authorized",
            "live_capital_authorized",
        ):
            self.assertIs(result[field], False)

    def test_both_accounts_need_the_comparison_sample(self):
        for name in ("baseline", "intervention"):
            with self.subTest(account=name):
                self.accounts[name]["completed_trades"] = self.minimum - 1
                result = self.assess()
                self.assertEqual(
                    result["comparison_criteria_status"],
                    "insufficient_evidence",
                )
                self.assertTrue(any(
                    name in reason
                    for reason in result["insufficient_evidence_reasons"]
                ))
                self.accounts[name]["completed_trades"] = self.minimum

    def test_intervention_must_meet_minimum_profit_factor(self):
        self.accounts["intervention"]["profit_factor"] = "1.15"
        result = self.assess()
        self.assertEqual(result["comparison_criteria_status"], "fail")
        self.assertIn(
            "minimum_intervention_profit_factor",
            result["failed_comparison_criteria"],
        )

    def test_equal_profit_factor_is_not_an_improvement(self):
        self.accounts["baseline"]["profit_factor"] = "1.3"
        result = self.assess()
        self.assertEqual(result["comparison_criteria_status"], "fail")
        self.assertIn(
            "positive_profit_factor_improvement",
            result["failed_comparison_criteria"],
        )

    def test_intervention_performance_failures_are_preserved(self):
        summary = self.accounts["intervention"]
        summary["return_percent"] = "-100"
        summary["maximum_marked_drawdown_percent"] = "101"
        result = self.assess()

        self.assertEqual(result["comparison_criteria_status"], "fail")
        for field in (
            "minimum_return_percent",
            "minimum_excess_return_percent",
            "maximum_drawdown_percent",
        ):
            self.assertIn(field, result["failed_comparison_criteria"])

    def test_insufficient_sample_keeps_failed_performance_checks(self):
        self.accounts["intervention"]["completed_trades"] = 0
        self.accounts["intervention"]["return_percent"] = "-100"
        result = self.assess()

        self.assertEqual(
            result["comparison_criteria_status"], "insufficient_evidence"
        )
        self.assertIn(
            "minimum_return_percent", result["failed_comparison_criteria"]
        )

    def test_undefined_profit_factor_remains_insufficient(self):
        for name in ("baseline", "intervention"):
            self.accounts[name]["profit_factor"] = None
            self.accounts[name]["profit_factor_status"] = "no_realized_losses"
        result = self.assess()
        self.assertEqual(
            result["comparison_criteria_status"], "insufficient_evidence"
        )
        self.assertIsNone(
            result["comparison_checks"][
                "positive_profit_factor_improvement"
            ]["passed"]
        )

    def test_missing_marks_or_availability_remain_insufficient(self):
        result = self.assess(availability_verified=False)
        self.assertEqual(
            result["comparison_criteria_status"], "insufficient_evidence"
        )

        self.accounts["intervention"]["has_equity_marks"] = False
        self.accounts["intervention"]["maximum_marked_drawdown_percent"] = None
        result = self.assess()
        self.assertEqual(
            result["comparison_criteria_status"], "insufficient_evidence"
        )
        self.assertEqual(
            result["accounts"]["intervention"]["performance_checks"], {}
        )

    def test_excessive_stale_coverage_remains_insufficient(self):
        self.accounts["baseline"]["data_quality"][
            "missing_or_stale_reference_ticks"
        ] = 100
        result = self.assess()
        self.assertEqual(
            result["comparison_criteria_status"], "insufficient_evidence"
        )

    def test_source_criteria_and_inputs_are_preserved(self):
        before_plan = deepcopy(self.plan)
        before_accounts = deepcopy(self.accounts)
        result = self.assess()

        self.assertEqual(result["source_criteria"], self.plan["criteria"])
        result["source_criteria"]["minimum_completed_trades"] = 1
        self.assertEqual(self.plan, before_plan)
        self.assertEqual(self.accounts, before_accounts)

    def test_invalid_or_weakened_settings_are_rejected(self):
        for changes in (
            {"minimum_completed_trades": True},
            {"minimum_completed_trades": 0},
            {"minimum_profit_factor": 1.2},
            {"minimum_profit_factor": "0"},
            {"minimum_profit_factor": "NaN"},
            {"availability_verified": 1},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.assess(**changes)


class CooldownAssessmentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = analysis_fixture.CooldownAnalysisIntegrationTests(
            methodName="test_baseline_metrics_match_existing_verified_analysis"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def assess(self, report=None):
        row = self.fixture.row
        return assess_cooldown_packet(
            self.fixture.report if report is None else report,
            policy=POLICY,
            minimum_completed_trades=100,
            minimum_profit_factor="1.2",
            expected_manifest=self.fixture.fixture.manifest,
            expected_plan_id=row["plan_id"],
            expected_sha256=row["registered_sha256"],
            expected_collection=row["provider_collection"],
        )

    def test_real_short_period_is_verified_but_insufficient(self):
        result = self.assess()
        self.assertEqual(result["verification"]["status"], "matched")
        self.assertEqual(
            result["comparison_criteria_status"], "insufficient_evidence"
        )
        self.assertEqual(result["validation_status"], "insufficient_evidence")
        self.assertEqual(len(result["input_report_sha256"]), 64)
        self.assertFalse(result["promotion_authorized"])

    def test_changed_report_cannot_receive_assessment(self):
        changed = deepcopy(self.fixture.report)
        changed["baseline"]["closed_trade_count"] = 100
        with self.assertRaises(ValueError):
            self.assess(changed)


if __name__ == "__main__":
    unittest.main()
