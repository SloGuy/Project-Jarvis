"""Tests for selecting held assets without requests or database access."""

from copy import deepcopy
import unittest

from app.capital.quote_provenance_universe import select_provenance_assets


def snapshot():
    return {
        "snapshot_at": "2026-09-25T12:00:00+00:00",
        "portfolios": [
            {
                "id": 1,
                "name": "Paper One",
                "portfolio_type": "paper",
                "is_active": True,
            },
            {
                "id": 2,
                "name": "Paper Two",
                "portfolio_type": "paper",
                "is_active": True,
            },
        ],
        "positions": [
            {"portfolio_id": 1, "asset_id": 7, "quantity": "2"},
            {"portfolio_id": 2, "asset_id": 7, "quantity": "3"},
            {"portfolio_id": 1, "asset_id": 212, "quantity": "1"},
        ],
        "assets": [
            {
                "id": 7,
                "symbol": "BTC",
                "asset_type": "crypto",
                "provider_id": "bitcoin",
                "is_active": True,
            },
            {
                "id": 212,
                "symbol": "CTVA",
                "asset_type": "stock",
                "provider_id": None,
                "is_active": True,
            },
        ],
    }


def resolved():
    return {
        1: {"portfolio_name": "Paper One"},
        2: {"portfolio_name": "Paper Two"},
    }


def select_assets(data=None, **changes):
    arguments = {
        "snapshot": snapshot() if data is None else data,
        "resolved_portfolios": resolved(),
        "crypto_provider_ids": {"BTC": "bitcoin"},
    }
    arguments.update(changes)
    return select_provenance_assets(**arguments)


class ProvenanceUniverseTests(unittest.TestCase):
    def test_shared_holdings_are_deduplicated(self):
        result = select_assets()
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["held_asset_count"], 2)
        self.assertEqual(len(result["assets"]), 2)
        self.assertEqual(result["assets"][0]["id"], 7)
        self.assertEqual(result["assets"][0]["portfolio_ids"], [1, 2])

    def test_stock_and_crypto_providers_are_explicit(self):
        result = select_assets()
        self.assertEqual(
            [row["provider"] for row in result["assets"]],
            ["CoinGecko REST", "Finnhub REST"],
        )

    def test_zero_positions_are_excluded(self):
        data = snapshot()
        data["positions"][2]["quantity"] = "0"
        result = select_assets(data)
        self.assertEqual(result["held_asset_count"], 1)
        self.assertEqual([row["id"] for row in result["assets"]], [7])

    def test_cash_only_universe_is_resolved_without_assets(self):
        data = snapshot()
        data["positions"] = []
        result = select_assets(data)
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["held_asset_count"], 0)
        self.assertEqual(result["assets"], [])

    def test_unheld_asset_metadata_does_not_expand_universe(self):
        data = snapshot()
        data["assets"].append({
            "id": 99,
            "symbol": "EXTRA",
            "asset_type": "stock",
            "provider_id": None,
            "is_active": True,
        })
        result = select_assets(data)
        self.assertEqual([row["id"] for row in result["assets"]], [7, 212])

    def test_missing_metadata_is_reported(self):
        data = snapshot()
        data["assets"] = data["assets"][:1]
        result = select_assets(data)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(
            result["unsupported_assets"][0]["reason"],
            "missing_asset_metadata",
        )

    def test_inactive_asset_is_reported(self):
        data = snapshot()
        data["assets"][1]["is_active"] = False
        result = select_assets(data)
        self.assertEqual(
            result["unsupported_assets"][0]["reason"], "inactive_asset"
        )

    def test_crypto_identity_mismatch_is_reported(self):
        data = snapshot()
        data["assets"][0]["provider_id"] = "wrong-bitcoin"
        result = select_assets(data)
        self.assertEqual(
            result["unsupported_assets"][0]["reason"],
            "crypto_provider_identity_unresolved",
        )
        self.assertEqual([row["id"] for row in result["assets"]], [212])

    def test_unknown_crypto_mapping_is_reported(self):
        result = select_assets(crypto_provider_ids={})
        self.assertEqual(
            result["unsupported_assets"][0]["reason"],
            "crypto_provider_identity_unresolved",
        )

    def test_invalid_symbol_and_unsupported_type_are_reported(self):
        for field, value, reason in (
            ("symbol", "", "invalid_asset_symbol"),
            ("asset_type", "bond", "unsupported_asset_type"),
        ):
            with self.subTest(field=field):
                data = snapshot()
                data["assets"][1][field] = value
                result = select_assets(data)
                self.assertEqual(
                    result["unsupported_assets"][0]["reason"], reason
                )

    def test_changed_portfolio_identity_or_eligibility_is_rejected(self):
        for field, value in (
            ("name", "Unexpected"),
            ("portfolio_type", "live"),
            ("is_active", False),
        ):
            with self.subTest(field=field):
                data = snapshot()
                data["portfolios"][0][field] = value
                with self.assertRaisesRegex(ValueError, "eligibility changed"):
                    select_assets(data)

    def test_missing_or_unexpected_portfolios_are_rejected(self):
        data = snapshot()
        data["portfolios"].pop()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            select_assets(data)

        data = snapshot()
        data["portfolios"][0]["id"] = 99
        with self.assertRaisesRegex(ValueError, "Unexpected portfolio"):
            select_assets(data)

    def test_duplicate_portfolios_assets_and_positions_are_rejected(self):
        for field in ("portfolios", "assets", "positions"):
            with self.subTest(field=field):
                data = snapshot()
                data[field].append(deepcopy(data[field][0]))
                with self.assertRaisesRegex(ValueError, "Duplicate"):
                    select_assets(data)

    def test_foreign_portfolio_holding_is_rejected(self):
        data = snapshot()
        data["positions"][0]["portfolio_id"] = 99
        with self.assertRaisesRegex(ValueError, "unexpected portfolio"):
            select_assets(data)

    def test_invalid_quantities_are_rejected(self):
        for value in ("-1", "NaN", "Infinity", True, None):
            with self.subTest(value=value):
                data = snapshot()
                data["positions"][0]["quantity"] = value
                with self.assertRaises(ValueError):
                    select_assets(data)

    def test_invalid_ids_and_missing_policy_are_rejected(self):
        for value in (True, 0, -1, "7"):
            with self.subTest(value=value):
                data = snapshot()
                data["positions"][0]["asset_id"] = value
                with self.assertRaises(ValueError):
                    select_assets(data)

        with self.assertRaises(ValueError):
            select_assets(resolved_portfolios={})
        with self.assertRaises(ValueError):
            select_assets(crypto_provider_ids=None)

    def test_inputs_are_preserved_and_output_is_deterministic(self):
        data = snapshot()
        mapping = resolved()
        original = deepcopy((data, mapping))
        first = select_assets(data, resolved_portfolios=mapping)
        self.assertEqual((data, mapping), original)

        data["positions"].reverse()
        data["assets"].reverse()
        data["portfolios"].reverse()
        self.assertEqual(first, select_assets(data, resolved_portfolios=mapping))

    def test_no_requests_or_authority_are_claimed(self):
        result = select_assets()
        for field in (
            "network_requests",
            "database_writes",
            "execution_authorized",
        ):
            self.assertFalse(result[field])


if __name__ == "__main__":
    unittest.main()
