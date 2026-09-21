"""Tests for indicative concentration and shared-holdings diagnostics."""

from copy import deepcopy
from decimal import Decimal
import unittest

from app.capital.portfolio_concentration import analyze_concentration


STAMP = "2026-09-21T12:00:00+00:00"


def holding(asset_id, quantity, value, symbol="BTC"):
    return {
        "asset_id": asset_id,
        "symbol": symbol,
        "quantity": quantity,
        "market_value_usd": value,
        "exclusion_reason": None,
    }


def portfolio(portfolio_id, cash, total, holdings):
    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": f"Paper {portfolio_id}",
        "measured_at": STAMP,
        "valuation_status": "indicative",
        "cash_balance_usd": cash,
        "total_value_usd": total,
        "holdings": holdings,
        "excluded_holdings": [],
    }


def valuation():
    return {
        "snapshot_at": STAMP,
        "portfolios": [
            portfolio(1, "80", "100", [holding(7, "2", "20")]),
            portfolio(2, "180", "200", [holding(7, "2", "20")]),
        ],
    }


class ConcentrationTests(unittest.TestCase):
    def test_per_portfolio_weights_include_cash(self):
        result = analyze_concentration(valuation())
        first, second = result["portfolios"]
        self.assertEqual(
            Decimal(first["concentration"]["cash_weight_percent"]), 80
        )
        self.assertEqual(
            Decimal(first["concentration"]["largest_asset_weight_percent"]),
            20,
        )
        self.assertEqual(
            Decimal(second["concentration"]["invested_weight_percent"]), 10
        )

    def test_combined_weights_use_equity_not_average_percentages(self):
        result = analyze_concentration(valuation())
        combined = result["combined"]["concentration"]
        self.assertEqual(result["combined"]["status"], "indicative")
        self.assertEqual(Decimal(combined["total_value_usd"]), 300)
        self.assertEqual(Decimal(combined["cash_balance_usd"]), 260)
        weight = Decimal(combined["assets"][0]["equity_weight_percent"])
        self.assertLess(abs(weight - Decimal("13.333333333333333333")), Decimal("1e-17"))

    def test_shared_holdings_identify_each_owner(self):
        result = analyze_concentration(valuation())
        shared = result["shared_holdings"]
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0]["asset_id"], 7)
        self.assertEqual(shared[0]["portfolio_count"], 2)
        self.assertEqual(
            [row["portfolio_id"] for row in shared[0]["holdings"]],
            [1, 2],
        )

    def test_missing_ctva_blocks_combined_but_preserves_overlap(self):
        data = valuation()
        first = data["portfolios"][0]
        first["valuation_status"] = "incomplete"
        first["total_value_usd"] = None
        missing = holding(212, "1", None, "CTVA")
        missing["exclusion_reason"] = "missing"
        first["holdings"].append(missing)
        first["excluded_holdings"] = [
            {"asset_id": 212, "reason": "missing"}
        ]

        result = analyze_concentration(data)

        self.assertEqual(result["combined"]["status"], "unavailable")
        self.assertIsNone(result["combined"]["concentration"])
        self.assertEqual(
            result["combined"]["blockers"],
            [{"portfolio_id": 1, "reason": "incomplete_valuation"}],
        )
        self.assertIsNone(result["portfolios"][0]["concentration"])
        self.assertIsNotNone(result["portfolios"][1]["concentration"])
        self.assertEqual(result["shared_holdings"][0]["asset_id"], 7)

    def test_shared_holdings_do_not_require_prices(self):
        data = valuation()
        for account in data["portfolios"]:
            account["valuation_status"] = "incomplete"
            account["total_value_usd"] = None
            account["holdings"][0]["market_value_usd"] = None
            account["holdings"][0]["exclusion_reason"] = "missing"

        result = analyze_concentration(data)
        self.assertIsNone(result["combined"]["concentration"])
        self.assertEqual(result["shared_holdings"][0]["portfolio_count"], 2)

    def test_cash_only_portfolio_has_zero_asset_concentration(self):
        data = {
            "snapshot_at": STAMP,
            "portfolios": [portfolio(1, "100", "100", [])],
        }
        result = analyze_concentration(data)
        weights = result["combined"]["concentration"]
        self.assertEqual(Decimal(weights["cash_weight_percent"]), 100)
        self.assertEqual(Decimal(weights["largest_asset_weight_percent"]), 0)
        self.assertEqual(weights["assets"], [])

    def test_zero_equity_has_no_percentage_weights(self):
        data = {
            "snapshot_at": STAMP,
            "portfolios": [portfolio(1, "0", "0", [])],
        }
        weights = analyze_concentration(data)["combined"]["concentration"]
        self.assertIsNone(weights["cash_weight_percent"])
        self.assertIsNone(weights["invested_weight_percent"])
        self.assertIsNone(weights["largest_asset_weight_percent"])

    def test_assets_are_ranked_by_value_with_stable_ties(self):
        data = {
            "snapshot_at": STAMP,
            "portfolios": [
                portfolio(1, "40", "100", [
                    holding(9, "1", "10", "XRP"),
                    holding(8, "1", "25", "ETH"),
                    holding(7, "1", "25", "BTC"),
                ]),
            ],
        }
        weights = analyze_concentration(data)["combined"]["concentration"]
        self.assertEqual(
            [row["asset_id"] for row in weights["assets"]],
            [7, 8, 9],
        )

    def test_mismatched_total_is_rejected(self):
        data = valuation()
        data["portfolios"][0]["total_value_usd"] = "999"
        with self.assertRaisesRegex(ValueError, "does not reconcile"):
            analyze_concentration(data)

    def test_false_complete_claim_is_rejected(self):
        for change in ("missing_price", "excluded_holding"):
            with self.subTest(change=change):
                data = valuation()
                first = data["portfolios"][0]
                if change == "missing_price":
                    first["holdings"][0]["market_value_usd"] = None
                else:
                    first["excluded_holdings"] = [{"asset_id": 7}]
                with self.assertRaisesRegex(ValueError, "excluded holdings"):
                    analyze_concentration(data)

    def test_duplicate_portfolios_and_holdings_are_rejected(self):
        data = valuation()
        data["portfolios"].append(deepcopy(data["portfolios"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate portfolio"):
            analyze_concentration(data)

        data = valuation()
        data["portfolios"][0]["holdings"].append(
            deepcopy(data["portfolios"][0]["holdings"][0])
        )
        with self.assertRaisesRegex(ValueError, "Duplicate holding"):
            analyze_concentration(data)

    def test_different_measurement_times_are_rejected(self):
        data = valuation()
        data["portfolios"][0]["measured_at"] = (
            "2026-09-21T11:00:00+00:00"
        )
        with self.assertRaisesRegex(ValueError, "measurement times differ"):
            analyze_concentration(data)

    def test_invalid_amounts_are_rejected(self):
        for field in ("cash_balance_usd", "total_value_usd"):
            for value in ("-1", "NaN", "Infinity", True, None):
                with self.subTest(field=field, value=value):
                    data = valuation()
                    data["portfolios"][0][field] = value
                    with self.assertRaises(ValueError):
                        analyze_concentration(data)

        for field in ("quantity", "market_value_usd"):
            for value in ("-1", "NaN", "Infinity", True):
                with self.subTest(field=field, value=value):
                    data = valuation()
                    data["portfolios"][0]["holdings"][0][field] = value
                    with self.assertRaises(ValueError):
                        analyze_concentration(data)

    def test_empty_input_has_no_combined_estimate(self):
        result = analyze_concentration({
            "snapshot_at": STAMP,
            "portfolios": [],
        })
        self.assertEqual(result["combined"]["status"], "unavailable")
        self.assertIsNone(result["combined"]["concentration"])
        self.assertEqual(result["shared_holdings"], [])

    def test_inputs_are_preserved_and_output_is_deterministic(self):
        data = valuation()
        original = deepcopy(data)
        result = analyze_concentration(data)
        self.assertEqual(data, original)
        self.assertEqual(result, analyze_concentration(data))

    def test_no_execution_or_allocation_authority(self):
        result = analyze_concentration(valuation())
        for field in (
            "database_writes",
            "allocation_authority",
            "live_capital_authority",
        ):
            self.assertFalse(result[field])


if __name__ == "__main__":
    unittest.main()
