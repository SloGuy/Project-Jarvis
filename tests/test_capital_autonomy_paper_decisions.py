"""Boundary tests for deterministic paper lifecycle decisions."""

import unittest

from app.capital.autonomy_paper_decisions import (
    MAXIMUM_ACTIVE_SECONDS,
    choose_paper_transition,
)


class PaperDecisionTests(unittest.TestCase):
    def decide(self, **changes):
        values = {
            "status": "active",
            "accounting_valid": True,
            "realized_gain_loss_usd": "0",
            "sell_fill_count": 0,
            "holding_count": 0,
            "active_age_seconds": 0,
            "activation_eligible": False,
        }
        values.update(changes)
        return choose_paper_transition(**values)

    def test_planned_requires_verified_eligibility(self):
        self.assertIsNone(self.decide(status="planned")["target"])
        self.assertEqual(self.decide(
            status="planned", activation_eligible=True
        )["target"], "active")

    def test_bad_accounting_blocks_activation(self):
        self.assertIsNone(self.decide(
            status="planned",
            accounting_valid=False,
            activation_eligible=True,
        )["target"])

    def test_bad_accounting_pauses_active_experiment(self):
        self.assertEqual(self.decide(
            accounting_valid=False
        )["target"], "paused")

    def test_realized_loss_exact_boundary_demotes(self):
        self.assertEqual(self.decide(
            realized_gain_loss_usd="-50"
        )["target"], "demoted")

    def test_loss_above_limit_demotes_without_sample_requirement(self):
        self.assertEqual(self.decide(
            realized_gain_loss_usd="-75",
            sell_fill_count=1,
        )["target"], "demoted")

    def test_loss_below_limit_with_small_sample_does_not_demote(self):
        self.assertIsNone(self.decide(
            realized_gain_loss_usd="-49.99999999",
            sell_fill_count=29,
        )["target"])

    def test_thirtieth_sell_fill_with_negative_result_demotes(self):
        self.assertEqual(self.decide(
            realized_gain_loss_usd="-0.00000001",
            sell_fill_count=30,
        )["target"], "demoted")

    def test_breakeven_and_profit_do_not_trigger_sample_rule(self):
        for realized in ("0", "25"):
            with self.subTest(realized=realized):
                self.assertIsNone(self.decide(
                    realized_gain_loss_usd=realized,
                    sell_fill_count=30,
                )["target"])

    def test_duration_boundary(self):
        self.assertIsNone(self.decide(
            active_age_seconds=MAXIMUM_ACTIVE_SECONDS - 1
        )["target"])
        self.assertEqual(self.decide(
            active_age_seconds=MAXIMUM_ACTIVE_SECONDS
        )["target"], "demoted")

    def test_demoted_with_holdings_waits_for_exits(self):
        self.assertIsNone(self.decide(
            status="demoted", holding_count=1
        )["target"])

    def test_empty_demoted_experiment_retires(self):
        self.assertEqual(self.decide(
            status="demoted"
        )["target"], "retired")

    def test_bad_accounting_prevents_automatic_retirement(self):
        self.assertIsNone(self.decide(
            status="demoted", accounting_valid=False
        )["target"])

    def test_pause_does_not_automatically_resume(self):
        self.assertIsNone(self.decide(
            status="paused", activation_eligible=True
        )["target"])

    def test_paused_experiment_can_be_demoted_for_loss(self):
        self.assertEqual(self.decide(
            status="paused", realized_gain_loss_usd="-50"
        )["target"], "demoted")

    def test_retired_experiment_stays_retired(self):
        self.assertIsNone(self.decide(
            status="retired", activation_eligible=True
        )["target"])

    def test_invalid_numeric_inputs_are_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity", True, None, "bad"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.decide(realized_gain_loss_usd=value)

    def test_invalid_counts_and_flags_are_rejected(self):
        cases = [
            {"sell_fill_count": True},
            {"sell_fill_count": -1},
            {"holding_count": 0.5},
            {"active_age_seconds": -1},
            {"accounting_valid": 1},
            {"activation_eligible": "yes"},
            {"status": "live"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.decide(**changes)

    def test_decision_never_grants_live_authority(self):
        for status in ("planned", "active", "paused", "demoted", "retired"):
            with self.subTest(status=status):
                result = self.decide(status=status, activation_eligible=True)
                self.assertIs(result["live_capital_authorized"], False)


if __name__ == "__main__":
    unittest.main()
