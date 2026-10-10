import copy
import unittest
from decimal import Decimal

from app.capital.trade_diagnosis import build_trade_diagnosis


class TradeDiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            self.row(1, "BTC", "fixed_mean_recovery", "10", "1"),
            self.row(2, "BTC", "recovery_timeout", "-4", "-2"),
            self.row(3, "ETH", "stop_loss", "-6", "-6"),
            self.row(4, "ETH", "recovery_timeout", "0", "0"),
        ]

    @staticmethod
    def row(identifier, symbol, exit_rule, pnl, return_percent):
        return {
            "id": identifier,
            "symbol": symbol,
            "status": "closed",
            "strategy_name": "mean_reversion_v2",
            "exit_rule": exit_rule,
            "realized_gain_loss_usd": pnl,
            "return_percent": return_percent,
            "opened_at": "2026-10-01T00:00:00+00:00",
            "closed_at": "2026-10-02T00:00:00+00:00",
        }

    def build(self, **changes):
        arguments = {
            "experiment_id": "mean_reversion_v2_paper_2026",
            "strategy_name": "mean_reversion_v2",
            "portfolio_id": 5,
            "journals": self.rows,
            "queried_at": "2026-10-08T23:00:00+00:00",
            "minimum_profit_factor": "1.20",
            "stop_loss_percent": "5",
            "minimum_closed_trades": 100,
        }
        arguments.update(changes)
        return build_trade_diagnosis(**arguments)

    def test_profit_factor_counts_and_expectancy(self):
        summary = self.build()["summary"]
        self.assertEqual(summary["closed_trades"], 4)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 2)
        self.assertEqual(summary["breakeven"], 1)
        self.assertEqual(Decimal(summary["gross_profit_usd"]), Decimal("10"))
        self.assertEqual(Decimal(summary["gross_loss_usd"]), Decimal("10"))
        self.assertEqual(Decimal(summary["net_realized_usd"]), Decimal("0"))
        self.assertEqual(Decimal(summary["profit_factor"]), Decimal("1"))
        self.assertEqual(Decimal(summary["expectancy_usd"]), Decimal("0"))
        self.assertIn(
            "Realized profit factor is below the supplied minimum.",
            self.build()["findings"],
        )

    def test_asset_and_exit_groups_reconcile(self):
        result = self.build()
        assets = {row["symbol"]: row for row in result["by_symbol"]}
        exits = {row["exit_rule"]: row for row in result["by_exit_rule"]}
        self.assertEqual(Decimal(assets["BTC"]["net_realized_usd"]), Decimal("6"))
        self.assertEqual(Decimal(assets["ETH"]["net_realized_usd"]), Decimal("-6"))
        self.assertEqual(exits["recovery_timeout"]["closed_trades"], 2)
        for groups in (assets.values(), exits.values()):
            self.assertEqual(
                sum(Decimal(row["net_realized_usd"]) for row in groups),
                Decimal(result["summary"]["net_realized_usd"]),
            )

    def test_stop_overrun_uses_percentage_points(self):
        rows = [
            self.row(1, "BTC", "stop_loss", "-5", "-5"),
            self.row(2, "ETH", "stop_loss", "-6", "-6"),
            self.row(3, "SOL", "recovery_timeout", "-8", "-8"),
            self.row(4, "XMR", "stop_loss", "-7", "-7"),
        ]
        overruns = self.build(journals=rows)["stop_overruns"]
        self.assertEqual([row["id"] for row in overruns], [4, 2])
        self.assertEqual(
            Decimal(overruns[0]["threshold_overrun_percentage_points"]),
            Decimal("2"),
        )

    def test_no_losses_does_not_invent_infinite_profit_factor(self):
        result = self.build(journals=[self.rows[0]])
        self.assertIsNone(result["summary"]["profit_factor"])
        self.assertEqual(
            result["summary"]["profit_factor_status"],
            "no_realized_losses",
        )

    def test_empty_sample_remains_insufficient(self):
        result = self.build(journals=[])
        self.assertEqual(result["summary"]["closed_trades"], 0)
        self.assertIsNone(result["summary"]["expectancy_usd"])
        self.assertIn(
            "Closed-trade sample is below the supplied minimum.",
            result["findings"],
        )

    def test_query_limit_is_reported(self):
        result = self.build(query_limit_reached=True)
        self.assertTrue(result["query_limit_reached"])
        self.assertIn(
            "Query limit reached; history may be incomplete.",
            result["findings"],
        )

    def test_duplicate_identity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate journal ID"):
            self.build(journals=[self.rows[0], self.rows[0]])

    def test_open_trade_and_wrong_strategy_are_rejected(self):
        for field, value in (
            ("status", "open"),
            ("strategy_name", "momentum_alignment_v1"),
        ):
            with self.subTest(field=field):
                rows = copy.deepcopy(self.rows)
                rows[0][field] = value
                with self.assertRaises(ValueError):
                    self.build(journals=rows)

    def test_missing_or_nonfinite_pnl_is_rejected(self):
        for value in (None, True, "NaN", "Infinity", "invalid"):
            with self.subTest(value=value):
                rows = copy.deepcopy(self.rows)
                rows[0]["realized_gain_loss_usd"] = value
                with self.assertRaises(ValueError):
                    self.build(journals=rows)

    def test_missing_stop_return_is_rejected(self):
        rows = copy.deepcopy(self.rows)
        rows[2]["return_percent"] = None
        with self.assertRaises(ValueError):
            self.build(journals=rows)

    def test_invalid_thresholds_are_rejected(self):
        for field, value in (
            ("minimum_profit_factor", "0"),
            ("minimum_profit_factor", True),
            ("stop_loss_percent", "-5"),
            ("minimum_closed_trades", True),
            ("query_limit_reached", "false"),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    self.build(**{field: value})

    def test_naive_query_timestamp_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.build(queried_at="2026-10-08T23:00:00")

    def test_source_hash_is_order_independent_and_detects_change(self):
        original = self.build()["journal_sha256"]
        reversed_hash = self.build(
            journals=list(reversed(self.rows))
        )["journal_sha256"]
        self.assertEqual(original, reversed_hash)
        rows = copy.deepcopy(self.rows)
        rows[0]["realized_gain_loss_usd"] = "11"
        self.assertNotEqual(
            original, self.build(journals=rows)["journal_sha256"]
        )

    def test_inputs_and_returned_results_are_independent(self):
        before = copy.deepcopy(self.rows)
        result = self.build()
        result["by_symbol"][0]["symbol"] = "CHANGED"
        self.assertEqual(self.rows, before)
        self.assertEqual(self.build()["by_symbol"][0]["symbol"], "BTC")

    def test_diagnosis_grants_no_authority(self):
        result = self.build()
        for field in (
            "validation_verified",
            "strategy_change_authorized",
            "promotion_authorized",
            "live_capital_authorized",
        ):
            self.assertIs(result[field], False)


if __name__ == "__main__":
    unittest.main()
