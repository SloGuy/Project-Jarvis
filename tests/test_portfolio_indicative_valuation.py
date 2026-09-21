"""Tests for indicative valuation and incomplete-price handling."""

from copy import deepcopy
from decimal import Decimal
import unittest

from app.capital.portfolio_daily_returns import build_daily_returns
from app.capital.portfolio_indicative_valuation import value_balance


MEASURED = "2026-09-20T23:59:59.999999+00:00"


def balance():
    return {
        "measured_at": MEASURED,
        "cash_balance_usd": "100",
        "positions": [
            {"asset_id": 7, "quantity": "2"},
            {"asset_id": 8, "quantity": "3"},
        ],
    }


def quote(asset_id, price, **changes):
    result = {
        "asset_id": asset_id,
        "measured_at": MEASURED,
        "provider": "test-provider",
        "observed_at": "2026-09-20T23:55:00+00:00",
        "status": "usable",
        "price_usd": price,
    }
    result.update(changes)
    return result


def quotes():
    return {
        7: quote(7, "10"),
        8: quote(8, "20"),
    }


class IndicativeValuationTests(unittest.TestCase):
    def test_complete_values_remain_indicative(self):
        result = value_balance(balance=balance(), quotes=quotes())
        self.assertEqual(result["valuation_status"], "indicative")
        self.assertEqual(Decimal(result["known_market_value_usd"]), 80)
        self.assertEqual(Decimal(result["total_value_usd"]), 180)
        self.assertEqual(result["excluded_holdings"], [])

    def test_missing_holding_prevents_partial_portfolio_total(self):
        result = value_balance(
            balance=balance(),
            quotes={7: quote(7, "10")},
        )
        self.assertEqual(result["valuation_status"], "incomplete")
        self.assertIsNone(result["total_value_usd"])
        self.assertEqual(Decimal(result["known_market_value_usd"]), 20)
        self.assertEqual(
            result["excluded_holdings"],
            [{"asset_id": 8, "reason": "missing_quote"}],
        )
        self.assertIsNone(result["holdings"][1]["market_value_usd"])

    def test_unusable_quotes_are_excluded_even_when_price_is_present(self):
        for status in ("stale", "invalid", "missing", "unknown"):
            with self.subTest(status=status):
                selected = quotes()
                selected[8]["status"] = status
                result = value_balance(balance=balance(), quotes=selected)
                self.assertIsNone(result["total_value_usd"])
                self.assertEqual(Decimal(result["known_market_value_usd"]), 20)
                self.assertEqual(
                    result["excluded_holdings"][0]["reason"], status
                )

    def test_cash_only_balance_needs_no_quotes(self):
        account = balance()
        account["positions"] = []
        result = value_balance(balance=account, quotes={})
        self.assertEqual(Decimal(result["total_value_usd"]), 100)
        self.assertEqual(result["holdings"], [])
        self.assertEqual(result["valuation_status"], "indicative")

    def test_zero_quantity_positions_do_not_require_prices(self):
        account = balance()
        account["positions"][1]["quantity"] = "0"
        result = value_balance(
            balance=account,
            quotes={7: quote(7, "10")},
        )
        self.assertEqual(Decimal(result["total_value_usd"]), 120)
        self.assertEqual(len(result["holdings"]), 1)

    def test_quote_for_wrong_asset_is_rejected(self):
        selected = quotes()
        selected[7]["asset_id"] = 9
        with self.assertRaisesRegex(ValueError, "different asset"):
            value_balance(balance=balance(), quotes=selected)

    def test_quote_for_wrong_measurement_is_rejected(self):
        selected = quotes()
        selected[7]["measured_at"] = "2026-09-19T23:59:59.999999+00:00"
        with self.assertRaisesRegex(ValueError, "different measurement"):
            value_balance(balance=balance(), quotes=selected)

    def test_future_dated_quote_is_rejected(self):
        selected = quotes()
        selected[7]["observed_at"] = "2026-09-21T00:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "future-dated"):
            value_balance(balance=balance(), quotes=selected)

    def test_usable_quote_requires_provider_and_timestamp(self):
        for field, value in (
            ("provider", None),
            ("provider", ""),
            ("provider", " "),
            ("observed_at", None),
        ):
            with self.subTest(field=field, value=value):
                selected = quotes()
                selected[7][field] = value
                with self.assertRaises(ValueError):
                    value_balance(balance=balance(), quotes=selected)

    def test_invalid_prices_are_rejected(self):
        for price in ("0", "-1", "NaN", "Infinity", None, True):
            with self.subTest(price=price):
                selected = quotes()
                selected[7]["price_usd"] = price
                with self.assertRaises(ValueError):
                    value_balance(balance=balance(), quotes=selected)

    def test_invalid_cash_and_quantities_are_rejected(self):
        for value in ("-1", "NaN", "Infinity", None, True):
            with self.subTest(value=value):
                account = balance()
                account["cash_balance_usd"] = value
                with self.assertRaises(ValueError):
                    value_balance(balance=account, quotes=quotes())

                account = balance()
                account["positions"][0]["quantity"] = value
                with self.assertRaises(ValueError):
                    value_balance(balance=account, quotes=quotes())

    def test_invalid_or_duplicate_position_ids_are_rejected(self):
        for asset_id in (True, 0, -1, "7", 8):
            with self.subTest(asset_id=asset_id):
                account = balance()
                account["positions"][0]["asset_id"] = asset_id
                with self.assertRaises(ValueError):
                    value_balance(balance=account, quotes=quotes())

    def test_decimal_values_preserve_small_amounts(self):
        account = balance()
        account["cash_balance_usd"] = "0.00000001"
        account["positions"] = [{"asset_id": 7, "quantity": "0.000000000001"}]
        result = value_balance(
            balance=account,
            quotes={7: quote(7, "0.00000001")},
        )
        self.assertEqual(
            Decimal(result["total_value_usd"]),
            Decimal("0.00000001000000000001"),
        )

    def test_input_is_preserved_and_result_is_deterministic(self):
        account = balance()
        selected = quotes()
        original = deepcopy((account, selected))

        result = value_balance(balance=account, quotes=selected)

        self.assertEqual((account, selected), original)
        self.assertEqual(
            result,
            value_balance(balance=account, quotes=selected),
        )

    def test_indicative_values_do_not_enter_daily_returns(self):
        first = value_balance(balance=balance(), quotes=quotes())
        second = deepcopy(first)
        second["measured_at"] = "2026-09-21T23:59:59.999999+00:00"
        second["total_value_usd"] = "190"

        result = build_daily_returns(
            points=[first, second],
            external_flow_times=[],
            as_of="2026-09-22T12:00:00+00:00",
        )

        self.assertEqual(result["return_count"], 0)
        self.assertIn(
            "unverified_valuation",
            result["excluded_intervals"][0]["reasons"],
        )

    def test_no_verification_or_authority_is_claimed(self):
        result = value_balance(balance=balance(), quotes=quotes())
        for field in (
            "market_quote_freshness_verified",
            "historical_availability_verified",
            "historical_completeness_verified",
            "database_writes",
            "allocation_authority",
            "live_capital_authority",
        ):
            with self.subTest(field=field):
                self.assertFalse(result[field])


if __name__ == "__main__":
    unittest.main()
