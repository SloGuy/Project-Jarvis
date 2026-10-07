"""Visibility and decision-boundary tests for provenance history."""

from copy import deepcopy
from datetime import timedelta
import unittest

import test_validation_quote_receipt as receipt_fixture

from app.capital.quote_provenance import make_quote_provenance
from app.capital.quote_provenance_store import save_quote_provenance
from app.capital.validation_quote_history import (
    ValidationQuoteHistory,
)
from app.capital.validation_quote_store import (
    ValidationQuoteReceiptStore,
)


class ValidationQuoteHistoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = (
            receipt_fixture.ValidationQuoteReceiptTests(
                "test_valid_receipt"
            )
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def history(self, receipts):
        return ValidationQuoteHistory(
            directory=self.fixture.directory,
            receipts=receipts,
            envelope=self.fixture.envelope,
            expected_sha256=self.fixture.sha,
            expected_bound_at=self.fixture.bound.isoformat(),
        )

    def window(self, history, decision=None):
        decision = decision or (
            self.fixture.recorded
            + timedelta(seconds=1)
        )

        return history.window(
            decision_at=decision.isoformat(),
            maximum_provider_age=timedelta(seconds=120),
            maximum_capture_age=timedelta(seconds=120),
            lookback=60,
        )

    def test_visible_receipt_produces_input(self):
        history = self.history([self.fixture.make()])
        result = self.window(history)

        self.assertEqual(result["status"], "eligible")
        self.assertEqual(result["visible_receipt_count"], 1)
        self.assertEqual(
            result["observations"][0]["first_recorded_at"],
            self.fixture.recorded.isoformat(),
        )
        self.assertEqual(
            result["latest_recorded_at"],
            self.fixture.recorded.isoformat(),
        )

    def test_capture_alone_does_not_establish_visibility(self):
        logged = (
            self.fixture.captured
            + timedelta(seconds=60)
        )
        receipt = self.fixture.make(
            recorded_at=logged.isoformat(),
        )
        history = self.history([receipt])

        decision = (
            self.fixture.captured
            + timedelta(seconds=30)
        )
        result = self.window(history, decision)

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["visible_receipt_count"], 0)
        self.assertEqual(result["observations"], [])

    def test_receipt_at_decision_is_not_visible(self):
        history = self.history([self.fixture.make()])
        result = self.window(
            history,
            self.fixture.recorded,
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["visible_receipt_count"], 0)

    def test_receipt_before_decision_is_visible(self):
        history = self.history([self.fixture.make()])
        decision = (
            self.fixture.recorded
            + timedelta(microseconds=1)
        )
        result = self.window(history, decision)

        self.assertEqual(result["status"], "eligible")

    def test_repeated_receipt_does_not_inflate_history(self):
        first = self.fixture.make()
        second = self.fixture.make(
            recorded_at=(
                self.fixture.recorded
                + timedelta(seconds=1)
            ).isoformat(),
        )
        history = self.history([first, second])
        result = self.window(
            history,
            self.fixture.recorded + timedelta(seconds=2),
        )

        self.assertEqual(result["visible_receipt_count"], 2)
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(
            result["observations"][0]["first_recorded_at"],
            self.fixture.recorded.isoformat(),
        )

    def test_stale_provider_time_remains_ineligible(self):
        history = self.history([self.fixture.make()])
        decision = (
            self.fixture.captured
            + timedelta(seconds=100)
        )
        result = self.window(history, decision)

        self.assertEqual(result["status"], "ineligible")
        self.assertIn(
            "provider_quote_too_old",
            result["eligibility"]["reasons"],
        )

    def test_before_plan_window_is_rejected(self):
        history = self.history([self.fixture.make()])
        decision = (
            self.fixture.start
            - timedelta(microseconds=1)
        )

        with self.assertRaisesRegex(ValueError, "outside"):
            self.window(history, decision)

    def test_at_plan_end_is_rejected(self):
        history = self.history([self.fixture.make()])

        with self.assertRaisesRegex(ValueError, "outside"):
            self.window(history, self.fixture.end)

    def test_out_of_order_receipts_are_rejected(self):
        first = self.fixture.make()
        second = self.fixture.make(
            recorded_at=(
                self.fixture.recorded
                + timedelta(seconds=1)
            ).isoformat(),
        )

        with self.assertRaisesRegex(ValueError, "increase"):
            self.history([second, first])

    def test_tampered_receipt_is_rejected(self):
        receipt = deepcopy(self.fixture.make())
        receipt["payload"]["recorded_at"] = (
            self.fixture.recorded
            + timedelta(seconds=1)
        ).isoformat()

        with self.assertRaises(ValueError):
            self.history([receipt])

    def test_future_logged_conflict_cannot_change_past_input(self):
        captured = (
            self.fixture.captured
            + timedelta(seconds=10)
        )

        record = make_quote_provenance(
            asset_id=1,
            symbol="BTC",
            asset_type="crypto",
            provider="CoinGecko REST",
            price_usd="60001",
            provider_timestamp=int(
                (
                    self.fixture.captured
                    - timedelta(seconds=30)
                ).timestamp()
            ),
            captured_at=captured,
        )

        ident = save_quote_provenance(
            directory=self.fixture.directory,
            record=record,
        )["record_id"]

        future_receipt = self.fixture.make(
            record_id=ident,
            recorded_at=(
                captured + timedelta(seconds=1)
            ).isoformat(),
        )

        history = self.history([
            self.fixture.make(),
            future_receipt,
        ])
        result = self.window(history)

        self.assertEqual(result["status"], "eligible")
        self.assertEqual(
            result["observations"][0]["price_usd"],
            "60000",
        )

    def test_sealed_collection_can_supply_history(self):
        store = ValidationQuoteReceiptStore(
            self.fixture.directory / "collection",
            envelope=self.fixture.envelope,
            expected_sha256=self.fixture.sha,
            bound_at=self.fixture.bound.isoformat(),
        )
        store.initialize()
        store.append(self.fixture.make())
        checkpoint = store.seal()

        history = self.history(store.export(checkpoint))
        result = self.window(history)

        self.assertEqual(result["status"], "eligible")
        self.assertTrue(result["receipt_bindings_verified"])
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["live_capital_authorized"])
        self.assertFalse(
            result["registry_authorization_verified"]
        )


if __name__ == "__main__":
    unittest.main()
