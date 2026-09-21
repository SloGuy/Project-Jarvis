"""Tests for connecting stored snapshots to indicative valuations."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import unittest

from app.capital.portfolio_snapshot_valuation import value_portfolio_snapshot


def snapshot():
    return {
        "snapshot_at": "2026-09-21T12:00:00+00:00",
        "portfolios": [
            {
                "id": 1,
                "name": "Paper One",
                "portfolio_type": "paper",
                "cash_balance_usd": "100",
            },
            {
                "id": 2,
                "name": "Paper Two",
                "portfolio_type": "paper",
                "cash_balance_usd": "200",
            },
        ],
        "positions": [
            {"portfolio_id": 1, "asset_id": 7, "quantity": "2"},
            {"portfolio_id": 2, "asset_id": 7, "quantity": "3"},
        ],
        "assets": [
            {"id": 7, "symbol": "BTC", "asset_type": "crypto"},
        ],
        "observations": [
            {
                "id": 10,
                "asset_id": 7,
                "provider": "CoinGecko",
                "observed_at": "2026-09-21T11:55:00+00:00",
                "price_usd": "10",
            },
        ],
    }


def value(data=None, **changes):
    arguments = {
        "snapshot": snapshot() if data is None else data,
        "provider_by_asset": {7: "CoinGecko"},
        "maximum_observation_age": timedelta(minutes=20),
    }
    arguments.update(changes)
    return value_portfolio_snapshot(**arguments)


class SnapshotValuationTests(unittest.TestCase):
    def test_shared_asset_is_valued_for_each_portfolio(self):
        result = value()
        self.assertEqual(result["complete_indicative_count"], 2)
        self.assertEqual(result["incomplete_count"], 0)
        self.assertEqual(
            [
                Decimal(row["total_value_usd"])
                for row in result["portfolios"]
            ],
            [Decimal("120"), Decimal("230")],
        )
        for portfolio in result["portfolios"]:
            holding = portfolio["holdings"][0]
            self.assertEqual(holding["symbol"], "BTC")
            self.assertEqual(holding["asset_type"], "crypto")
            self.assertEqual(holding["provider"], "CoinGecko")
            self.assertEqual(portfolio["valuation_status"], "indicative")

    def test_missing_provider_selection_is_explicit(self):
        result = value(provider_by_asset={})
        self.assertEqual(result["incomplete_count"], 2)
        for portfolio in result["portfolios"]:
            self.assertIsNone(portfolio["total_value_usd"])
            self.assertEqual(
                portfolio["holdings"][0]["coverage_note"],
                "provider_not_selected",
            )

    def test_other_provider_is_not_used_as_fallback(self):
        data = snapshot()
        data["observations"][0]["provider"] = "Other"
        result = value(data)
        self.assertEqual(result["incomplete_count"], 2)
        for portfolio in result["portfolios"]:
            self.assertIsNone(portfolio["total_value_usd"])
            self.assertEqual(
                portfolio["excluded_holdings"][0]["reason"], "missing"
            )

    def test_missing_asset_metadata_prevents_valuation(self):
        data = snapshot()
        data["assets"] = []
        result = value(data)
        for portfolio in result["portfolios"]:
            self.assertIsNone(portfolio["total_value_usd"])
            self.assertEqual(
                portfolio["holdings"][0]["coverage_note"],
                "missing_asset_metadata",
            )

    def test_stale_observation_prevents_portfolio_total(self):
        data = snapshot()
        data["observations"][0]["observed_at"] = (
            "2026-09-21T11:00:00+00:00"
        )
        result = value(data)
        self.assertEqual(result["incomplete_count"], 2)
        for portfolio in result["portfolios"]:
            self.assertIsNone(portfolio["total_value_usd"])
            self.assertEqual(
                portfolio["excluded_holdings"][0]["reason"], "stale"
            )

    def test_future_observation_is_not_used(self):
        data = snapshot()
        data["observations"].append({
            **data["observations"][0],
            "id": 11,
            "observed_at": "2026-09-21T12:01:00+00:00",
            "price_usd": "999",
        })
        result = value(data)
        self.assertEqual(
            Decimal(result["portfolios"][0]["total_value_usd"]), 120
        )

    def test_incomplete_portfolio_does_not_hide_complete_portfolio(self):
        data = snapshot()
        data["positions"].append({
            "portfolio_id": 1,
            "asset_id": 8,
            "quantity": "1",
        })
        data["assets"].append({
            "id": 8,
            "symbol": "ETH",
            "asset_type": "crypto",
        })
        result = value(data, provider_by_asset={
            7: "CoinGecko",
            8: "CoinGecko",
        })
        self.assertEqual(result["incomplete_count"], 1)
        self.assertEqual(result["complete_indicative_count"], 1)
        self.assertIsNone(result["portfolios"][0]["total_value_usd"])
        self.assertEqual(
            Decimal(result["portfolios"][1]["total_value_usd"]), 230
        )

    def test_cash_only_portfolio_needs_no_provider(self):
        data = snapshot()
        data["positions"] = []
        data["assets"] = []
        data["observations"] = []
        result = value(data, provider_by_asset={})
        self.assertEqual(result["complete_indicative_count"], 2)
        self.assertEqual(
            [
                Decimal(row["total_value_usd"])
                for row in result["portfolios"]
            ],
            [Decimal("100"), Decimal("200")],
        )

    def test_duplicate_metadata_or_portfolios_are_rejected(self):
        for field in ("assets", "portfolios"):
            with self.subTest(field=field):
                data = snapshot()
                data[field].append(deepcopy(data[field][0]))
                with self.assertRaisesRegex(ValueError, "Duplicate"):
                    value(data)

    def test_unknown_portfolio_position_is_rejected(self):
        data = snapshot()
        data["positions"][0]["portfolio_id"] = 99
        with self.assertRaisesRegex(ValueError, "unknown portfolio"):
            value(data)

    def test_duplicate_position_is_rejected(self):
        data = snapshot()
        data["positions"].append(deepcopy(data["positions"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate position"):
            value(data)

    def test_nonpaper_portfolio_is_rejected(self):
        data = snapshot()
        data["portfolios"][0]["portfolio_type"] = "live"
        with self.assertRaisesRegex(ValueError, "Only paper"):
            value(data)

    def test_provider_policy_and_age_are_validated(self):
        for policy in (
            None,
            {True: "CoinGecko"},
            {0: "CoinGecko"},
            {"7": "CoinGecko"},
            {7: ""},
            {7: None},
        ):
            with self.subTest(policy=policy):
                with self.assertRaises(ValueError):
                    value(provider_by_asset=policy)

        for age in (None, 1200, timedelta(0), timedelta(seconds=-1)):
            with self.subTest(age=age):
                with self.assertRaises(ValueError):
                    value(maximum_observation_age=age)

    def test_inputs_are_preserved_and_output_is_deterministic(self):
        data = snapshot()
        original = deepcopy(data)
        result = value(data)
        self.assertEqual(data, original)
        self.assertEqual(result, value(data))

    def test_no_market_freshness_or_authority_is_claimed(self):
        result = value()
        self.assertEqual(
            result["valuation_basis"], "stored_observations_indicative"
        )
        for field in (
            "market_quote_freshness_verified",
            "historical_availability_verified",
            "database_writes",
            "allocation_authority",
            "live_capital_authority",
        ):
            with self.subTest(field=field):
                self.assertFalse(result[field])


if __name__ == "__main__":
    unittest.main()
