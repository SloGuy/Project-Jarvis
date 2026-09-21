"""Tests for read-only portfolio transaction normalization."""

from copy import deepcopy
from decimal import Decimal
import unittest

from app.capital.portfolio_transaction_effects import (
    normalize_transactions,
    transaction_effect,
)


def transaction(**changes):
    record = {
        "id": 1,
        "created_at": "2026-09-20T12:00:00+00:00",
        "transaction_type": "buy",
        "asset_id": 7,
        "quantity": "2",
        "price_usd": "50",
        "total_usd": "100",
        "fees_usd": "1",
    }
    record.update(changes)
    return record


def cash_transaction(kind, **changes):
    record = transaction(
        transaction_type=kind,
        asset_id=None,
        quantity="100",
        price_usd="1",
        total_usd="100",
        fees_usd="0",
    )
    record.update(changes)
    return record


class TransactionEffectTests(unittest.TestCase):
    def test_buy_includes_fees(self):
        result = transaction_effect(transaction())
        self.assertEqual(Decimal(result["cash_delta_usd"]), Decimal("-101"))
        self.assertEqual(Decimal(result["quantity_delta"]), Decimal("2"))
        self.assertFalse(result["external_cash_flow"])

    def test_sell_deducts_fees(self):
        result = transaction_effect(
            transaction(transaction_type="sell")
        )
        self.assertEqual(Decimal(result["cash_delta_usd"]), Decimal("99"))
        self.assertEqual(Decimal(result["quantity_delta"]), Decimal("-2"))
        self.assertFalse(result["external_cash_flow"])

    def test_deposit_is_external_inflow(self):
        result = transaction_effect(cash_transaction("deposit"))
        self.assertEqual(Decimal(result["cash_delta_usd"]), Decimal("100"))
        self.assertEqual(Decimal(result["quantity_delta"]), Decimal("0"))
        self.assertIsNone(result["asset_id"])
        self.assertTrue(result["external_cash_flow"])

    def test_withdrawal_is_external_outflow(self):
        result = transaction_effect(cash_transaction("withdrawal"))
        self.assertEqual(Decimal(result["cash_delta_usd"]), Decimal("-100"))
        self.assertEqual(Decimal(result["quantity_delta"]), Decimal("0"))
        self.assertTrue(result["external_cash_flow"])

    def test_stored_total_controls_cash_effect(self):
        result = transaction_effect(
            transaction(
                quantity="3",
                price_usd="0.333333333",
                total_usd="1.00000000",
                fees_usd="0.00000001",
            )
        )
        self.assertEqual(
            Decimal(result["cash_delta_usd"]),
            Decimal("-1.00000001"),
        )

    def test_sale_fees_cannot_exceed_proceeds(self):
        with self.assertRaises(ValueError):
            transaction_effect(
                transaction(transaction_type="sell", fees_usd="101")
            )

        result = transaction_effect(
            transaction(transaction_type="sell", fees_usd="100")
        )
        self.assertEqual(Decimal(result["cash_delta_usd"]), Decimal("0"))

    def test_invalid_numeric_values_are_rejected(self):
        for field in ("quantity", "price_usd", "total_usd", "fees_usd"):
            for value in (True, None, "NaN", "Infinity", "-Infinity", "bad"):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        transaction_effect(transaction(**{field: value}))

    def test_invalid_signs_are_rejected(self):
        for field, values in (
            ("quantity", ("0", "-1")),
            ("price_usd", ("0", "-1")),
            ("total_usd", ("-1",)),
            ("fees_usd", ("-1",)),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        transaction_effect(transaction(**{field: value}))

    def test_invalid_identifiers_are_rejected(self):
        for field in ("id", "asset_id"):
            for value in (True, None, 0, -1, "7", 7.0):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        transaction_effect(transaction(**{field: value}))

    def test_invalid_cash_records_are_rejected(self):
        invalid_changes = (
            {"asset_id": 7},
            {"quantity": "99"},
            {"price_usd": "2"},
            {"fees_usd": "1"},
            {"quantity": "0", "total_usd": "0"},
        )
        for kind in ("deposit", "withdrawal"):
            for changes in invalid_changes:
                with self.subTest(kind=kind, changes=changes):
                    with self.assertRaises(ValueError):
                        transaction_effect(
                            cash_transaction(kind, **changes)
                        )

    def test_unknown_transaction_type_is_rejected(self):
        for kind in ("reset", "dividend", "", None):
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    transaction_effect(
                        transaction(transaction_type=kind)
                    )

    def test_timestamps_require_timezone_and_normalize_to_utc(self):
        with self.assertRaises(ValueError):
            transaction_effect(
                transaction(created_at="2026-09-20T12:00:00")
            )

        result = transaction_effect(
            transaction(created_at="2026-09-20T08:00:00-04:00")
        )
        self.assertEqual(
            result["created_at"],
            "2026-09-20T12:00:00+00:00",
        )

    def test_duplicate_transaction_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_transactions([
                transaction(),
                transaction(created_at="2026-09-21T12:00:00+00:00"),
            ])

    def test_sorting_is_chronological_then_by_id_without_mutation(self):
        records = [
            transaction(id=3, created_at="2026-09-21T12:00:00+00:00"),
            transaction(id=2),
            transaction(id=1),
        ]
        original = deepcopy(records)

        result = normalize_transactions(records)

        self.assertEqual(
            [row["transaction_id"] for row in result],
            [1, 2, 3],
        )
        self.assertEqual(records, original)
        self.assertEqual(result, normalize_transactions(records))

    def test_missing_required_fields_are_rejected(self):
        for field in transaction():
            record = transaction()
            del record[field]
            with self.subTest(field=field):
                with self.assertRaises(KeyError):
                    transaction_effect(record)

    def test_empty_ledger_returns_empty_list(self):
        self.assertEqual(normalize_transactions([]), [])


if __name__ == "__main__":
    unittest.main()
