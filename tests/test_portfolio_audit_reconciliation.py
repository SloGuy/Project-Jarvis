"""Pure reconciliation tests; no database access."""
from copy import deepcopy
from decimal import Decimal
import unittest

from app.capital.portfolio_audit_reconciliation import (
    reconcile_audit_snapshot,
)


def image(amount):
    return {"id": 1, "cash_balance_usd": Decimal(amount)}


def event(number, operation, before, after):
    return {
        "event_id": number,
        "source_table": "portfolios",
        "source_row_id": 1,
        "operation": operation,
        "before": before,
        "after": after,
    }


def snapshot(events, amount="100.00000001"):
    current = []
    if amount is not None:
        current = [{
            "source_row_id": 1,
            "row_json": (
                '{"id":1,"cash_balance_usd":' + amount + '}'
            ),
        }]
    return {
        "installation": {"installation_id": "test-installation"},
        "sampled_at": "2026-09-27T08:00:00+00:00",
        "events": events,
        "current_rows": {
            "portfolios": current,
            "portfolio_positions": [],
            "portfolio_transactions": [],
        },
    }


class PortfolioAuditReconciliationTests(unittest.TestCase):
    def reconcile(self, events, amount="100.00000001"):
        return reconcile_audit_snapshot(snapshot(events, amount))

    def assert_issue(self, result, reason):
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["issues"][0]["reason"], reason)

    def test_baseline_matches_exact_decimal(self):
        result = self.reconcile([
            event(1, "BASELINE", None, image("100.00000001")),
        ])
        self.assertEqual(result["status"], "row_images_match")
        self.assertEqual(result["matched_row_count"], 1)

    def test_small_decimal_difference_is_detected(self):
        result = self.reconcile([
            event(1, "BASELINE", None, image("100.00000002")),
        ])
        self.assert_issue(result, "final_image_differs_from_current_row")

    def test_image_chain_does_not_use_event_id_order(self):
        result = self.reconcile([
            event(90, "BASELINE", None, image("100")),
            event(2, "UPDATE", image("200"), image("300")),
            event(50, "UPDATE", image("100"), image("200")),
        ], "300")
        self.assertEqual(result["status"], "row_images_match")

    def test_insert_after_installation(self):
        result = self.reconcile([
            event(1, "INSERT", None, image("100.00000001")),
        ])
        self.assertEqual(result["status"], "row_images_match")

    def test_delete_matches_absent_current_row(self):
        result = self.reconcile([
            event(1, "BASELINE", None, image("100")),
            event(2, "DELETE", image("100"), None),
        ], None)
        self.assertEqual(result["status"], "row_images_match")

    def test_deleted_row_still_present_is_detected(self):
        result = self.reconcile([
            event(1, "BASELINE", None, image("100")),
            event(2, "DELETE", image("100"), None),
        ], "100")
        self.assert_issue(result, "final_image_differs_from_current_row")

    def test_current_row_without_audit_is_detected(self):
        self.assert_issue(
            self.reconcile([]),
            "final_image_differs_from_current_row",
        )

    def test_missing_intermediate_update_is_detected(self):
        result = self.reconcile([
            event(1, "BASELINE", None, image("100")),
            event(3, "UPDATE", image("200"), image("300")),
        ], "300")
        self.assert_issue(result, "broken_row_image_chain")

    def test_competing_changes_are_ambiguous(self):
        result = self.reconcile([
            event(1, "BASELINE", None, image("100")),
            event(2, "UPDATE", image("100"), image("200")),
            event(3, "UPDATE", image("100"), image("300")),
        ], "300")
        self.assert_issue(result, "ambiguous_row_image_chain")

    def test_revisited_state_is_conservatively_ambiguous(self):
        result = self.reconcile([
            event(1, "BASELINE", None, image("100")),
            event(2, "UPDATE", image("100"), image("200")),
            event(3, "UPDATE", image("200"), image("100")),
            event(4, "UPDATE", image("100"), image("300")),
        ], "300")
        self.assert_issue(result, "ambiguous_row_image_chain")

    def test_duplicate_baseline_is_unresolved(self):
        result = self.reconcile([
            event(1, "BASELINE", None, image("100")),
            event(2, "BASELINE", None, image("100")),
        ], "100")
        self.assert_issue(result, "duplicate_baseline")

    def test_changed_row_identity_is_unresolved(self):
        old = image("100")
        old["id"] = 2
        result = self.reconcile([
            event(1, "UPDATE", old, image("100")),
        ], "100")
        self.assert_issue(
            result,
            "row_identity_change_requires_separate_handling",
        )

    def test_duplicate_event_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate audit event"):
            self.reconcile([
                event(1, "BASELINE", None, image("100")),
                event(1, "UPDATE", image("100"), image("200")),
            ], "200")

    def test_duplicate_current_row_is_rejected(self):
        data = snapshot([])
        rows = data["current_rows"]["portfolios"]
        rows.append(deepcopy(rows[0]))
        with self.assertRaisesRegex(ValueError, "Duplicate current row"):
            reconcile_audit_snapshot(data)

    def test_malformed_current_images_are_rejected(self):
        for raw in (
            "{",
            "[]",
            '{"id":true}',
            '{"id":1,"id":1}',
            '{"id":1,"cash_balance_usd":NaN}',
            '{"id":2,"cash_balance_usd":100}',
        ):
            with self.subTest(raw=raw):
                data = snapshot([])
                data["current_rows"]["portfolios"][0]["row_json"] = raw
                with self.assertRaises(ValueError):
                    reconcile_audit_snapshot(data)

    def test_reset_style_deletions_and_cash_update(self):
        data = snapshot([
            event(1, "BASELINE", None, image("100")),
            event(2, "UPDATE", image("100"), image("1000")),
        ], "1000")
        for source, row_id in (
            ("portfolio_positions", 10),
            ("portfolio_transactions", 20),
        ):
            row = {"id": row_id, "portfolio_id": 1}
            for operation, before, after in (
                ("BASELINE", None, row),
                ("DELETE", row, None),
            ):
                data["events"].append({
                    "event_id": len(data["events"]) + 1,
                    "source_table": source,
                    "source_row_id": row_id,
                    "operation": operation,
                    "before": before,
                    "after": after,
                })
        result = reconcile_audit_snapshot(data)
        self.assertEqual(result["status"], "row_images_match")
        self.assertEqual(result["matched_row_count"], 3)

    def test_input_is_unchanged_and_authority_is_not_granted(self):
        data = snapshot([
            event(1, "BASELINE", None, image("100.00000001")),
        ])
        original = deepcopy(data)
        result = reconcile_audit_snapshot(data)
        self.assertEqual(data, original)
        for name in (
            "historical_completeness_verified",
            "commit_order_verified",
            "database_writes",
            "execution_authorized",
        ):
            self.assertIs(result[name], False)


if __name__ == "__main__":
    unittest.main()
