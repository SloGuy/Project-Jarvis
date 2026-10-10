import copy
import unittest

from app.capital.trade_research_design import (
    HYPOTHESIS,
    build_research_design,
    validate_design_hypothesis,
    verify_design,
)


class TradeResearchDesignTests(unittest.TestCase):
    def setUp(self):
        self.diagnosis = {
            "designation": "retrospective_trade_diagnosis",
            "experiment_id": "mr2-paper",
            "strategy_name": "mean_reversion_v2",
            "journal_sha256": "a" * 64,
            "configuration_sha256": "b" * 64,
            "thresholds": {
                "minimum_profit_factor": "1.20",
                "minimum_closed_trades": 100,
                "stop_loss_percent": "5",
            },
        }

    def build(self):
        return build_research_design(self.diagnosis)

    def test_design_is_bound_to_originating_evidence(self):
        result = self.build()
        self.assertEqual(result["origin_experiment_id"], "mr2-paper")
        self.assertEqual(result["strategy_name"], "mean_reversion_v2")
        self.assertEqual(result["origin_journal_sha256"], "a" * 64)
        self.assertEqual(result["origin_configuration_sha256"], "b" * 64)
        self.assertEqual(result["hypothesis"], HYPOTHESIS)

    def test_intervention_is_fixed_and_preserves_protective_exits(self):
        intervention = self.build()["intervention"]
        self.assertEqual(intervention["cooldown_seconds"], 3600)
        self.assertEqual(
            intervention["scope"], "same_asset_within_the_test_strategy"
        )
        self.assertIn("available before", intervention["trigger"])
        self.assertIn("exactly 3600 seconds", intervention["rule"])
        self.assertEqual(
            intervention["protective_exits"], "Remain enabled and unchanged."
        )
        self.assertEqual(
            intervention["other_entry_and_risk_rules"], "Remain unchanged."
        )

    def test_comparison_uses_separate_accounts_and_prospective_inputs(self):
        comparison = self.build()["comparison"]
        self.assertIn("prospectively collected", comparison["inputs"])
        self.assertIn("Separate simulated accounts", comparison["accounts"])
        self.assertIn(
            "intervention account", comparison["cooldown_history"]
        )
        self.assertIn("available before", comparison["cooldown_history"])
        self.assertIn("fixed before collection", comparison["costs"])
        self.assertIn("no exclusions", comparison["asset_universe"])

    def test_acceptance_thresholds_are_preserved_as_observations(self):
        result = self.build()
        self.assertEqual(
            result["observed_committee_thresholds"],
            self.diagnosis["thresholds"],
        )
        self.assertFalse(result["validation_ready"])
        self.assertTrue(result["registration_blockers"])

    def test_matching_design_verifies(self):
        result = self.build()
        self.assertEqual(verify_design(result, self.diagnosis), result)

    def test_changed_intervention_is_rejected(self):
        result = self.build()
        result["intervention"]["cooldown_seconds"] = 1800
        with self.assertRaisesRegex(ValueError, "predefined comparison"):
            verify_design(result, self.diagnosis)

    def test_rehashed_changed_design_is_still_rejected(self):
        from app.capital.trade_research_design import _digest

        result = self.build()
        result["intervention"]["cooldown_seconds"] = 1800
        body = {
            key: value for key, value in result.items()
            if key != "design_sha256"
        }
        result["design_sha256"] = _digest(body)
        with self.assertRaisesRegex(ValueError, "predefined comparison"):
            verify_design(result, self.diagnosis)

    def test_changed_origin_changes_design_hash(self):
        original = self.build()["design_sha256"]
        self.diagnosis["journal_sha256"] = "c" * 64
        self.assertNotEqual(original, self.build()["design_sha256"])

    def test_only_the_predefined_hypothesis_is_accepted(self):
        validate_design_hypothesis(HYPOTHESIS)
        validate_design_hypothesis(" " + HYPOTHESIS + " ")
        for value in (
            None,
            "Does excluding the eventual losing trades improve returns?",
            "Does stop-loss overrun cause the failure?",
            HYPOTHESIS.replace("60-minute", "30-minute"),
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "departed"):
                    validate_design_hypothesis(value)

    def test_wrong_diagnosis_scope_is_rejected(self):
        self.diagnosis["designation"] = "verified_validation"
        with self.assertRaisesRegex(ValueError, "designation"):
            self.build()

    def test_missing_identity_is_rejected(self):
        for field in (
            "experiment_id",
            "strategy_name",
            "journal_sha256",
            "configuration_sha256",
        ):
            with self.subTest(field=field):
                diagnosis = copy.deepcopy(self.diagnosis)
                diagnosis.pop(field)
                with self.assertRaises(ValueError):
                    build_research_design(diagnosis)

    def test_returned_design_does_not_mutate_diagnosis(self):
        original = copy.deepcopy(self.diagnosis)
        result = self.build()
        result["observed_committee_thresholds"]["minimum_profit_factor"] = "1"
        self.assertEqual(self.diagnosis, original)

    def test_design_grants_no_authority_or_claim_of_benefit(self):
        result = self.build()
        for field in (
            "historical_benefit_established",
            "validation_ready",
            "strategy_change_authorized",
            "promotion_authorized",
            "live_capital_authorized",
        ):
            self.assertIs(result[field], False)


if __name__ == "__main__":
    unittest.main()
