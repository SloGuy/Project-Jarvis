"""Tests for audit row decoding; no database access."""
from copy import deepcopy
from decimal import Decimal
import unittest

from app.capital.portfolio_audit_events import decode_audit_event


INSTALLATION = "12345678-1234-5678-1234-567812345678"


def sample_event():
    return {
        "event_id": 10,
        "installation_id": INSTALLATION,
        "database_transaction_id": 100,
        "recorded_at": "2026-09-27T08:00:00+00:00",
        "source_schema": "public",
        "source_table": "portfolio_positions",
        "operation": "INSERT",
        "source_row_id": 7,
        "old_portfolio_id": None,
        "new_portfolio_id": 3,
        "old_row_json": None,
        "new_row_json": (
            '{"id":7,"portfolio_id":3,'
            '"quantity":0.123456789123,'
            '"average_cost_usd":123456789012.12345678}'
        ),
    }


def decode(event):
    return decode_audit_event(
        event=event,
        schema="public",
        installation_id=INSTALLATION,
    )


class PortfolioAuditEventTests(unittest.TestCase):
    def test_exact_decimal_precision_and_input_preservation(self):
        event = sample_event()
        original = deepcopy(event)
        result = decode(event)
        self.assertEqual(
            result["after"]["quantity"],
            Decimal("0.123456789123"),
        )
        self.assertEqual(
            result["after"]["average_cost_usd"],
            Decimal("123456789012.12345678"),
        )
        self.assertIsInstance(result["after"]["quantity"], Decimal)
        self.assertEqual(event, original)

    def test_all_operations_have_expected_images(self):
        for operation in ("BASELINE", "INSERT", "UPDATE", "DELETE"):
            with self.subTest(operation=operation):
                event = sample_event()
                event["operation"] = operation
                if operation in ("UPDATE", "DELETE"):
                    event["old_row_json"] = event["new_row_json"]
                    event["old_portfolio_id"] = 3
                if operation == "DELETE":
                    event["new_row_json"] = None
                    event["new_portfolio_id"] = None
                result = decode(event)
                self.assertEqual(result["operation"], operation)
                self.assertEqual(
                    result["before"] is not None,
                    operation in ("UPDATE", "DELETE"),
                )
                self.assertEqual(
                    result["after"] is not None,
                    operation != "DELETE",
                )

    def test_portfolio_row_uses_its_own_id(self):
        event = sample_event()
        event.update(
            source_table="portfolios",
            source_row_id=3,
            new_row_json='{"id":3,"cash_balance_usd":1000.00000001}',
        )
        result = decode(event)
        self.assertEqual(result["new_portfolio_id"], 3)
        self.assertEqual(
            result["after"]["cash_balance_usd"],
            Decimal("1000.00000001"),
        )

    def test_transaction_source_is_supported(self):
        event = sample_event()
        event["source_table"] = "portfolio_transactions"
        event["new_row_json"] = (
            '{"id":7,"portfolio_id":3,"asset_id":null,'
            '"transaction_type":"deposit","total_usd":10.00000001}'
        )
        self.assertEqual(
            decode(event)["after"]["total_usd"],
            Decimal("10.00000001"),
        )

    def test_update_preserves_changed_row_and_portfolio_ids(self):
        event = sample_event()
        event.update(
            operation="UPDATE",
            old_portfolio_id=1,
            old_row_json='{"id":6,"portfolio_id":1}',
        )
        result = decode(event)
        self.assertEqual(result["before"]["id"], 6)
        self.assertEqual(result["after"]["id"], 7)
        self.assertEqual(result["old_portfolio_id"], 1)
        self.assertEqual(result["new_portfolio_id"], 3)

    def test_wrong_bindings_are_rejected(self):
        for field, value in (
            ("installation_id", "87654321-4321-8765-4321-876543218765"),
            ("source_schema", "other"),
            ("source_table", "unrelated"),
            ("operation", "TRUNCATE"),
        ):
            with self.subTest(field=field):
                event = sample_event()
                event[field] = value
                with self.assertRaises(ValueError):
                    decode(event)

    def test_invalid_event_identifiers_are_rejected(self):
        for field in (
            "event_id",
            "database_transaction_id",
            "source_row_id",
            "new_portfolio_id",
        ):
            for value in (True, 0, -1, "7", 7.0, None):
                with self.subTest(field=field, value=value):
                    event = sample_event()
                    event[field] = value
                    with self.assertRaises(ValueError):
                        decode(event)

    def test_invalid_row_identifiers_are_rejected(self):
        for raw in (
            '{"id":true,"portfolio_id":3}',
            '{"id":0,"portfolio_id":3}',
            '{"id":"7","portfolio_id":3}',
            '{"id":7.0,"portfolio_id":3}',
            '{"id":7,"portfolio_id":false}',
            '{"id":7,"portfolio_id":0}',
            '{"id":7}',
        ):
            with self.subTest(raw=raw):
                event = sample_event()
                event["new_row_json"] = raw
                with self.assertRaises(ValueError):
                    decode(event)

    def test_image_identity_mismatches_are_rejected(self):
        for raw in (
            '{"id":8,"portfolio_id":3}',
            '{"id":7,"portfolio_id":4}',
        ):
            with self.subTest(raw=raw):
                event = sample_event()
                event["new_row_json"] = raw
                with self.assertRaises(ValueError):
                    decode(event)

    def test_missing_or_extra_images_are_rejected(self):
        cases = [
            {"new_row_json": None},
            {"old_row_json": '{"id":7,"portfolio_id":3}'},
            {"operation": "UPDATE"},
            {"operation": "DELETE"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                event = sample_event()
                event.update(changes)
                with self.assertRaises(ValueError):
                    decode(event)

    def test_portfolio_id_without_image_is_rejected(self):
        event = sample_event()
        event["old_portfolio_id"] = 3
        with self.assertRaises(ValueError):
            decode(event)

    def test_malformed_or_non_object_json_is_rejected(self):
        for raw in ("{", "[]", "null", "42", '"text"', {"id": 7}):
            with self.subTest(raw=raw):
                event = sample_event()
                event["new_row_json"] = raw
                with self.assertRaises(ValueError):
                    decode(event)

    def test_duplicate_json_fields_are_rejected(self):
        for raw in (
            '{"id":7,"id":7,"portfolio_id":3}',
            '{"id":7,"portfolio_id":3,"nested":{"x":1,"x":2}}',
        ):
            with self.subTest(raw=raw):
                event = sample_event()
                event["new_row_json"] = raw
                with self.assertRaises(ValueError):
                    decode(event)

    def test_nonfinite_json_constants_are_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                event = sample_event()
                event["new_row_json"] = (
                    '{"id":7,"portfolio_id":3,"quantity":' + value + '}'
                )
                with self.assertRaises(ValueError):
                    decode(event)

    def test_naive_timestamp_is_rejected(self):
        event = sample_event()
        event["recorded_at"] = "2026-09-27T08:00:00"
        with self.assertRaises(ValueError):
            decode(event)

    def test_empty_expected_schema_is_rejected(self):
        with self.assertRaises(ValueError):
            decode_audit_event(
                event=sample_event(),
                schema="",
                installation_id=INSTALLATION,
            )


if __name__ == "__main__":
    unittest.main()
