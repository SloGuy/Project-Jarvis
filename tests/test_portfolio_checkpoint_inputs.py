"""Pure checkpoint-input tests; no database access."""
from copy import deepcopy
from decimal import Decimal
import json
import unittest

from app.capital.portfolio_checkpoint_inputs import build_checkpoint_inputs


def row(identifier, image):
    return {
        "source_row_id": identifier,
        "row_json": json.dumps(image),
    }


def fixture():
    return {
        "sampled_at": "2026-09-27T08:30:00+00:00",
        "database_snapshot": "repeatable_read",
        "database_read_only": True,
        "asset_metadata_requested": True,
        "installation": {"installation_id": "test-installation"},
        "visibility_snapshot": "100:110:105",
        "current_rows": {
            "portfolios": [
                row(1, {
                    "id": 1,
                    "name": "Test Paper",
                    "portfolio_type": "paper",
                    "is_active": True,
                    "cash_balance_usd": "100.00000001",
                }),
                row(2, {
                    "id": 2,
                    "name": "Unselected",
                    "portfolio_type": "paper",
                    "is_active": True,
                    "cash_balance_usd": "500",
                }),
            ],
            "portfolio_positions": [
                row(10, {
                    "id": 10,
                    "portfolio_id": 1,
                    "asset_id": 7,
                    "quantity": "0.123456789123",
                }),
                row(11, {
                    "id": 11,
                    "portfolio_id": 2,
                    "asset_id": 8,
                    "quantity": "2",
                }),
            ],
            "portfolio_transactions": [],
        },
        "asset_rows": [
            row(7, {
                "id": 7,
                "symbol": "BTC",
                "asset_type": "crypto",
                "is_active": True,
            }),
            row(8, {
                "id": 8,
                "symbol": "ETH",
                "asset_type": "crypto",
                "is_active": True,
            }),
        ],
    }


class CheckpointInputTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()
        self.resolved = {
            1: {
                "portfolio_name": "Test Paper",
                "strategy_name": "test_strategy",
                "experiment_id": "test_experiment",
            },
        }

    def build(self):
        return build_checkpoint_inputs(
            audit_snapshot=self.data,
            resolved_portfolios=self.resolved,
        )

    def change_image(self, table, index, **changes):
        records = (
            self.data["asset_rows"]
            if table == "assets"
            else self.data["current_rows"][table]
        )
        image = json.loads(records[index]["row_json"])
        image.update(changes)
        records[index]["row_json"] = json.dumps(image)

    def test_selects_only_registered_portfolio_and_held_assets(self):
        result = self.build()
        self.assertEqual([p["id"] for p in result["portfolios"]], [1])
        self.assertEqual([p["id"] for p in result["positions"]], [10])
        self.assertEqual([a["id"] for a in result["assets"]], [7])

    def test_numeric_json_preserves_decimal_precision(self):
        self.data["current_rows"]["portfolios"][0]["row_json"] = (
            '{"id":1,"name":"Test Paper","portfolio_type":"paper",'
            '"is_active":true,"cash_balance_usd":100.00000001}'
        )
        self.data["current_rows"]["portfolio_positions"][0]["row_json"] = (
            '{"id":10,"portfolio_id":1,"asset_id":7,'
            '"quantity":0.123456789123}'
        )
        result = self.build()
        self.assertEqual(
            result["portfolios"][0]["cash_balance_usd"],
            Decimal("100.00000001"),
        )
        self.assertEqual(
            result["positions"][0]["quantity"],
            Decimal("0.123456789123"),
        )

    def test_retains_actual_snapshot_identity(self):
        result = self.build()
        self.assertEqual(result["snapshot_at"], self.data["sampled_at"])
        self.assertEqual(result["audit_visibility_snapshot"], "100:110:105")
        self.assertEqual(result["audit_installation_id"], "test-installation")

    def test_missing_asset_metadata_remains_missing(self):
        self.data["asset_rows"] = []
        result = self.build()
        self.assertEqual(result["assets"], [])
        self.assertEqual(len(result["positions"]), 1)

    def test_zero_position_does_not_select_asset(self):
        self.change_image("portfolio_positions", 0, quantity="0")
        self.assertEqual(self.build()["assets"], [])

    def test_inactive_held_asset_and_position_are_preserved(self):
        self.change_image("assets", 0, is_active=False)
        original = deepcopy(self.data)
        result = self.build()
        self.assertEqual(result["assets"][0]["id"], 7)
        self.assertIs(result["assets"][0]["is_active"], False)
        self.assertEqual(result["positions"][0]["asset_id"], 7)
        self.assertEqual(
            result["positions"][0]["quantity"], "0.123456789123"
        )
        self.assertEqual(self.data, original)

    def test_changed_portfolio_identity_or_eligibility_is_rejected(self):
        for changes in (
            {"name": "Different"},
            {"portfolio_type": "live"},
            {"is_active": False},
        ):
            with self.subTest(changes=changes):
                self.data = fixture()
                self.change_image("portfolios", 0, **changes)
                with self.assertRaisesRegex(ValueError, "eligibility changed"):
                    self.build()

    def test_missing_registered_portfolio_is_rejected(self):
        self.data["current_rows"]["portfolios"] = []
        with self.assertRaisesRegex(ValueError, "absent"):
            self.build()

    def test_snapshot_requirements_are_enforced(self):
        for field, value in (
            ("database_snapshot", "read_committed"),
            ("database_read_only", False),
            ("asset_metadata_requested", False),
        ):
            with self.subTest(field=field):
                self.data = fixture()
                self.data[field] = value
                with self.assertRaises(ValueError):
                    self.build()

    def test_invalid_portfolio_selection_is_rejected(self):
        for selection in ({}, {True: {}}, {"1": {}}, {0: {}}, None):
            with self.subTest(selection=selection):
                self.resolved = selection
                with self.assertRaises(ValueError):
                    self.build()

    def test_duplicate_rows_are_rejected(self):
        self.data["asset_rows"].append(
            deepcopy(self.data["asset_rows"][0])
        )
        with self.assertRaisesRegex(ValueError, "Duplicate row identity"):
            self.build()

    def test_row_identity_mismatch_is_rejected(self):
        self.data["current_rows"]["portfolios"][0]["source_row_id"] = 99
        with self.assertRaisesRegex(ValueError, "identity differs"):
            self.build()

    def test_invalid_position_quantities_are_rejected(self):
        for value in ("-1", "NaN", "Infinity", True):
            with self.subTest(value=value):
                self.data = fixture()
                self.change_image("portfolio_positions", 0, quantity=value)
                with self.assertRaises(ValueError):
                    self.build()

    def test_invalid_position_identifiers_are_rejected(self):
        for field in ("portfolio_id", "asset_id"):
            with self.subTest(field=field):
                self.data = fixture()
                self.change_image("portfolio_positions", 0, **{field: True})
                with self.assertRaises(ValueError):
                    self.build()

    def test_malformed_row_images_are_rejected(self):
        for raw in (
            "{",
            "[]",
            '{"id":1,"id":1}',
            '{"id":1,"cash_balance_usd":NaN}',
        ):
            with self.subTest(raw=raw):
                self.data = fixture()
                self.data["current_rows"]["portfolios"][0]["row_json"] = raw
                with self.assertRaises(ValueError):
                    self.build()

    def test_input_preserved_and_no_history_claim_added(self):
        original = deepcopy((self.data, self.resolved))
        result = self.build()
        self.assertEqual((self.data, self.resolved), original)
        self.assertTrue(result["database_read_only"])
        self.assertFalse(result["historical_completeness_verified"])
        self.assertFalse(result["execution_authorized"])
        self.assertEqual(result["transactions"], [])
        self.assertEqual(result["observations"], [])


if __name__ == "__main__":
    unittest.main()
