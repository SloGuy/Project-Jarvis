import copy
import unittest
from unittest.mock import patch

from app.capital import trade_diagnosis_collection as collector


class TradeDiagnosisCollectionTests(unittest.TestCase):
    def setUp(self):
        self.experiment = {
            "experiment_id": "mr2-paper",
            "strategy_name": "mean_reversion_v2",
            "portfolio_name": "MR2 Paper",
            "status": "running",
            "execution_mode": "autonomous_paper_trading",
            "risk_policy_name": "mr2-policy",
        }
        self.portfolio = {
            "id": 5,
            "name": "MR2 Paper",
            "portfolio_type": "paper",
        }
        self.policy = {
            "name": "mr2-policy",
            "stop_loss_percent": "5",
        }
        self.thresholds = {
            "minimum_closed_trades": 100,
            "minimum_profit_factor": "1.20",
        }
        self.query = {
            "status": "success",
            "portfolio_id": 5,
            "filter": "closed",
            "count": 2,
            "journals": [
                {
                    "id": 1,
                    "symbol": "BTC",
                    "status": "closed",
                    "strategy_name": "mean_reversion_v2",
                    "exit_rule": "fixed_mean_recovery",
                    "realized_gain_loss_usd": "2",
                    "return_percent": "1",
                },
                {
                    "id": 2,
                    "symbol": "ETH",
                    "status": "closed",
                    "strategy_name": "mean_reversion_v2",
                    "exit_rule": "stop_loss",
                    "realized_gain_loss_usd": "-3",
                    "return_percent": "-6",
                },
            ],
        }
        self.mocks = {}
        for name, value in (
            ("_authorize", None),
            ("read_experiment", self.experiment),
            ("read_portfolio", self.portfolio),
            ("read_risk_policy", self.policy),
            ("read_committee_thresholds", self.thresholds),
            ("read_closed_journals", self.query),
        ):
            patcher = patch.object(collector, name, return_value=value)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def collect(self):
        return collector.collect_trade_diagnosis(experiment_id="mr2-paper")

    def test_matching_binding_produces_diagnosis(self):
        result = self.collect()
        self.assertEqual(result["portfolio_id"], 5)
        self.assertEqual(result["experiment_id"], "mr2-paper")
        self.assertEqual(result["summary"]["closed_trades"], 2)
        self.assertEqual(result["summary"]["net_realized_usd"], "-1")
        self.assertEqual(result["thresholds"]["stop_loss_percent"], "5")
        self.assertEqual(
            result["thresholds"]["minimum_profit_factor"], "1.20"
        )
        self.assertEqual(len(result["configuration_sha256"]), 64)
        self.assertTrue(result["configuration_rechecked"])
        self.mocks["read_portfolio"].assert_called_with("MR2 Paper")
        self.mocks["read_risk_policy"].assert_called_with("mr2-policy")
        self.mocks["read_closed_journals"].assert_called_once_with(5)
        self.assertEqual(self.mocks["_authorize"].call_count, 2)

    def test_authority_denial_prevents_reads(self):
        self.mocks["_authorize"].side_effect = PermissionError("disabled")
        with self.assertRaises(PermissionError):
            self.collect()
        self.mocks["read_experiment"].assert_not_called()
        self.mocks["read_closed_journals"].assert_not_called()

    def test_authority_is_rechecked_after_query(self):
        self.mocks["_authorize"].side_effect = [
            None, PermissionError("disabled"),
        ]
        with self.assertRaises(PermissionError):
            self.collect()
        self.mocks["read_closed_journals"].assert_called_once()

    def test_live_experiment_is_rejected_before_query(self):
        self.experiment["execution_mode"] = "live"
        with self.assertRaisesRegex(ValueError, "paper execution"):
            self.collect()
        self.mocks["read_closed_journals"].assert_not_called()

    def test_ineligible_experiment_status_is_rejected(self):
        self.experiment["status"] = "planned"
        with self.assertRaisesRegex(ValueError, "operating history"):
            self.collect()
        self.mocks["read_closed_journals"].assert_not_called()

    def test_wrong_experiment_identity_is_rejected(self):
        self.experiment["experiment_id"] = "another-experiment"
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.collect()

    def test_wrong_or_nonpaper_portfolio_is_rejected(self):
        for field, value in (
            ("name", "Another Portfolio"),
            ("portfolio_type", "live"),
            ("id", True),
        ):
            with self.subTest(field=field):
                portfolio = copy.deepcopy(self.portfolio)
                portfolio[field] = value
                self.mocks["read_portfolio"].return_value = portfolio
                with self.assertRaisesRegex(ValueError, "Portfolio binding"):
                    self.collect()
        self.mocks["read_closed_journals"].assert_not_called()

    def test_wrong_policy_identity_is_rejected(self):
        self.policy["name"] = "another-policy"
        with self.assertRaisesRegex(ValueError, "Risk-policy binding"):
            self.collect()
        self.mocks["read_closed_journals"].assert_not_called()

    def test_query_metadata_must_match(self):
        for field, value in (
            ("status", "failed"),
            ("portfolio_id", 1),
            ("filter", "open"),
            ("count", 99),
            ("journals", None),
        ):
            with self.subTest(field=field):
                query = copy.deepcopy(self.query)
                query[field] = value
                self.mocks["read_closed_journals"].return_value = query
                with self.assertRaisesRegex(ValueError, "Journal query"):
                    self.collect()

    def test_mixed_strategy_journals_are_not_silently_removed(self):
        self.query["journals"][0]["strategy_name"] = "momentum_alignment_v1"
        with self.assertRaisesRegex(ValueError, "strategy mismatch"):
            self.collect()

    def test_configuration_change_is_rejected(self):
        changed = copy.deepcopy(self.policy)
        changed["stop_loss_percent"] = "10"
        self.mocks["read_risk_policy"].side_effect = [self.policy, changed]
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            self.collect()

    def test_committee_threshold_change_is_rejected(self):
        changed = copy.deepcopy(self.thresholds)
        changed["minimum_profit_factor"] = "1.10"
        self.mocks["read_committee_thresholds"].side_effect = [
            self.thresholds, changed,
        ]
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            self.collect()

    def test_query_limit_is_preserved(self):
        with patch.object(collector, "QUERY_LIMIT", 2):
            result = self.collect()
        self.assertTrue(result["query_limit_reached"])

    def test_collection_does_not_mutate_inputs_or_grant_authority(self):
        original = copy.deepcopy(self.query)
        result = self.collect()
        result["configuration"]["risk_policy"]["name"] = "changed"
        self.assertEqual(self.query, original)
        self.assertEqual(self.policy["name"], "mr2-policy")
        for field in (
            "simultaneous_snapshot",
            "database_writes",
            "research_state_writes",
            "validation_verified",
            "strategy_change_authorized",
            "promotion_authorized",
            "live_capital_authorized",
        ):
            self.assertIs(result[field], False)

    def test_blank_or_untrimmed_experiment_id_is_rejected(self):
        for value in ("", " mr2-paper", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    collector.collect_trade_diagnosis(experiment_id=value)
        self.mocks["_authorize"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
