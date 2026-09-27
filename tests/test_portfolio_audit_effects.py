"""Pure accounting-effect tests; no database access."""
from copy import deepcopy
import unittest

from app.capital.portfolio_audit_effects import assess_audit_effects


STAMP = "2026-09-27T08:00:00+00:00"


def event(identifier, source, operation, before, after, transaction=100):
    return {
        "event_id": identifier,
        "database_transaction_id": transaction,
        "source_table": source,
        "operation": operation,
        "before": before,
        "after": after,
    }


def portfolio(cash):
    return {
        "id": 1,
        "portfolio_type": "paper",
        "cash_balance_usd": cash,
    }


def position(quantity):
    return {
        "id": 10,
        "portfolio_id": 1,
        "asset_id": 7,
        "quantity": quantity,
    }


def ledger(kind="buy", **changes):
    row = {
        "id": 20,
        "portfolio_id": 1,
        "asset_id": 7,
        "transaction_type": kind,
        "quantity": "2",
        "price_usd": "50",
        "total_usd": "100",
        "fees_usd": "1",
        "created_at": STAMP,
    }
    row.update(changes)
    return row


def buy_events():
    return [
        event(1, "portfolios", "UPDATE", portfolio("1000"), portfolio("899")),
        event(2, "portfolio_positions", "INSERT", None, position("2")),
        event(3, "portfolio_transactions", "INSERT", None, ledger()),
    ]


def snapshot(events):
    return {
        "installation": {"installation_id": "test"},
        "sampled_at": STAMP,
        "events": events,
    }


class PortfolioAuditEffectsTests(unittest.TestCase):
    def assess(self, events):
        return assess_audit_effects(snapshot(events))

    def assert_reason(self, events, reason):
        result = self.assess(events)
        self.assertEqual(result["status"], "unresolved")
        self.assertIn(reason, result["transaction_groups"][0]["reasons"])

    def test_buy_includes_fees(self):
        result = self.assess(buy_events())
        self.assertEqual(result["status"], "amounts_match")
        self.assertEqual(
            result["transaction_groups"][0]["inserted_ledger_count"], 1
        )

    def test_sell_uses_net_proceeds(self):
        events = [
            event(1, "portfolios", "UPDATE", portfolio("899"), portfolio("998")),
            event(2, "portfolio_positions", "DELETE", position("2"), None),
            event(3, "portfolio_transactions", "INSERT", None, ledger("sell")),
        ]
        self.assertEqual(self.assess(events)["status"], "amounts_match")

    def test_exact_recorded_total_is_used(self):
        events = buy_events()
        events[0]["after"]["cash_balance_usd"] = "899.99999999"
        events[1]["after"]["quantity"] = "0.123456789123"
        events[2]["after"].update(
            quantity="0.123456789123",
            price_usd="810",
            total_usd="100.00000001",
            fees_usd="0",
        )
        self.assertEqual(self.assess(events)["status"], "amounts_match")

    def test_cash_mismatch(self):
        events = buy_events()
        events[0]["after"]["cash_balance_usd"] = "900"
        self.assert_reason(events, "cash_delta_mismatch")

    def test_quantity_mismatch(self):
        events = buy_events()
        events[1]["after"]["quantity"] = "1.999999999999"
        self.assert_reason(events, "quantity_delta_mismatch")

    def test_missing_ledger_entry(self):
        self.assert_reason(buy_events()[:2], "cash_delta_mismatch")

    def test_missing_cash_change(self):
        self.assert_reason(buy_events()[1:], "cash_delta_mismatch")

    def test_deposit_and_withdrawal_are_external_flows(self):
        for kind, final_cash in (("deposit", "1100"), ("withdrawal", "900")):
            with self.subTest(kind=kind):
                events = [
                    event(
                        1, "portfolios", "UPDATE",
                        portfolio("1000"), portfolio(final_cash),
                    ),
                    event(
                        2, "portfolio_transactions", "INSERT", None,
                        ledger(
                            kind,
                            asset_id=None,
                            quantity="100",
                            price_usd="1",
                            fees_usd="0",
                        ),
                    ),
                ]
                result = self.assess(events)
                self.assertEqual(result["status"], "amounts_match")
                flow = result["transaction_groups"][0]["external_flows"][0]
                self.assertEqual(flow["transaction_type"], kind)
                self.assertEqual(flow["ledger_created_at"], STAMP)
                self.assertFalse(flow["commit_time_verified"])

    def test_reset_is_unresolved(self):
        events = [
            event(1, "portfolios", "UPDATE", portfolio("899"), portfolio("1000")),
            event(2, "portfolio_positions", "DELETE", position("2"), None),
            event(3, "portfolio_transactions", "DELETE", ledger(), None),
        ]
        self.assert_reason(events, "ledger_update_or_deletion")

    def test_edited_ledger_is_unresolved(self):
        self.assert_reason([
            event(
                1, "portfolio_transactions", "UPDATE",
                ledger(), ledger(total_usd="99"),
            ),
        ], "ledger_update_or_deletion")

    def test_new_portfolio_is_unresolved(self):
        self.assert_reason([
            event(1, "portfolios", "INSERT", None, portfolio("1000")),
        ], "portfolio_creation_or_deletion")

    def test_position_transfer_is_unresolved(self):
        before = position("2")
        after = {**before, "portfolio_id": 2}
        self.assert_reason([
            event(1, "portfolio_positions", "UPDATE", before, after),
        ], "position_identity_changed")

    def test_row_id_change_is_unresolved(self):
        after = {**portfolio("1000"), "id": 2}
        self.assert_reason([
            event(1, "portfolios", "UPDATE", portfolio("1000"), after),
        ], "row_identity_changed")

    def test_nonpaper_portfolio_is_unresolved(self):
        events = buy_events()
        events[0]["after"]["portfolio_type"] = "live"
        self.assert_reason(events, "nonpaper_portfolio")

    def test_unsupported_ledger_type_is_unresolved(self):
        events = buy_events()
        events[2]["after"]["transaction_type"] = "adjustment"
        self.assert_reason(events, "invalid_or_unsupported_accounting_evidence")

    def test_duplicate_event_id_is_rejected(self):
        events = buy_events()
        events[1]["event_id"] = events[0]["event_id"]
        with self.assertRaisesRegex(ValueError, "Duplicate audit event"):
            self.assess(events)

    def test_duplicate_inserted_ledger_id_is_unresolved(self):
        events = buy_events()
        events.append(event(
            4, "portfolio_transactions", "INSERT", None, ledger()
        ))
        self.assert_reason(events, "invalid_or_unsupported_accounting_evidence")

    def test_baseline_does_not_count_as_activity(self):
        result = self.assess([
            event(1, "portfolios", "BASELINE", None, portfolio("1000")),
        ])
        self.assertEqual(result["status"], "no_changes")
        self.assertEqual(result["transaction_groups"], [])

    def test_groups_do_not_cancel_each_others_mismatches(self):
        events = buy_events()
        events[0]["database_transaction_id"] = 200
        result = self.assess(events)
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(len(result["transaction_groups"]), 2)
        self.assertTrue(all(
            row["status"] == "unresolved"
            for row in result["transaction_groups"]
        ))

    def test_input_preserved_and_authority_not_granted(self):
        data = snapshot(buy_events())
        original = deepcopy(data)
        result = assess_audit_effects(data)
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
