"""Capture-cycle tests using temporary storage and mocked fetching."""

from datetime import timedelta
import unittest
from unittest.mock import patch

import test_validation_provider_collection as collection_fixture

from app.capital.quote_provenance import make_quote_provenance
from app.capital.quote_provenance_store import save_quote_provenance
from app.capital.validation_provider_capture import (
    ProviderCaptureError,
    capture_provider_cycle,
)
from app.capital.validation_provider_collection import (
    bind_provider_collection,
)


class ValidationProviderCaptureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = (
            collection_fixture.ValidationProviderCollectionTests(
                "test_binding_records_checkpoint_and_history"
            )
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

        self.base = self.fixture.fixture
        self.plan_id = self.fixture.plan_id

        self.authorize = self.fixture.patches.enter_context(
            patch(
                "app.capital.validation_provider_capture."
                "authorize_collection"
            )
        )
        self.binding = self.fixture.patches.enter_context(
            patch(
                "app.capital.validation_provider_capture."
                "check_current_binding"
            )
        )
        self.fetch = self.fixture.patches.enter_context(
            patch(
                "app.capital.validation_provider_capture."
                "capture_plan_quote",
                side_effect=self.fake_capture,
            )
        )

    def fake_capture(self, plan, directory):
        captured = self.fixture.now.return_value
        record = make_quote_provenance(
            asset_id=plan["asset_id"],
            symbol=plan["symbol"],
            asset_type="crypto",
            provider="CoinGecko REST",
            price_usd="60000",
            provider_timestamp=int(
                (
                    captured - timedelta(seconds=30)
                ).timestamp()
            ),
            captured_at=captured,
        )
        return save_quote_provenance(
            directory=directory,
            record=record,
        )

    def prepare_capture(self):
        bind_provider_collection(self.plan_id)
        self.fixture.now.return_value = self.base.captured

    def count(self):
        return self.fixture.row()["provider_collection"][
            "store_checkpoint"
        ]["count"]

    def test_waits_before_warmup(self):
        self.fixture.now.return_value = (
            self.base.start
            - timedelta(minutes=30, seconds=1)
        )
        result = capture_provider_cycle(self.plan_id)

        self.assertEqual(
            result["status"],
            "waiting_for_collection_window",
        )
        self.fetch.assert_not_called()
        self.assertNotIn(
            "provider_collection",
            self.fixture.row(),
        )

    def test_first_cycle_binds_without_fetching(self):
        result = capture_provider_cycle(self.plan_id)

        self.assertEqual(result["status"], "bound")
        self.fetch.assert_not_called()
        self.assertEqual(self.count(), 0)

    def test_capture_updates_retained_checkpoint(self):
        self.prepare_capture()
        result = capture_provider_cycle(self.plan_id)

        self.assertEqual(result["status"], "captured")
        self.assertEqual(result["receipt_count"], 1)
        self.assertEqual(self.count(), 1)
        self.fetch.assert_called_once()
        self.assertFalse(result["live_capital_authorized"])

    def test_immediate_repeat_does_not_fetch(self):
        self.prepare_capture()
        capture_provider_cycle(self.plan_id)
        result = capture_provider_cycle(self.plan_id)

        self.assertEqual(
            result["status"],
            "waiting_for_capture",
        )
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.fetch.call_count, 1)

    def test_capture_resumes_at_interval_boundary(self):
        self.prepare_capture()
        capture_provider_cycle(self.plan_id)
        self.fixture.now.return_value += timedelta(seconds=60)

        result = capture_provider_cycle(self.plan_id)

        self.assertEqual(result["status"], "captured")
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.fetch.call_count, 2)

    def test_provider_failure_does_not_append(self):
        self.prepare_capture()
        self.fetch.side_effect = ProviderCaptureError(
            "isolated provider failure"
        )

        with self.assertRaises(ProviderCaptureError):
            capture_provider_cycle(self.plan_id)

        self.assertEqual(self.count(), 0)

    def test_denied_authority_prevents_fetch(self):
        self.prepare_capture()
        self.authorize.side_effect = PermissionError(
            "isolated denial"
        )

        with self.assertRaises(PermissionError):
            capture_provider_cycle(self.plan_id)

        self.fetch.assert_not_called()
        self.assertEqual(self.count(), 0)

    def test_binding_mismatch_prevents_fetch(self):
        self.prepare_capture()
        self.binding.side_effect = ValueError(
            "isolated binding mismatch"
        )

        with self.assertRaises(ValueError):
            capture_provider_cycle(self.plan_id)

        self.fetch.assert_not_called()
        self.assertEqual(self.count(), 0)

    def test_end_of_window_seals_without_fetch(self):
        self.prepare_capture()
        capture_provider_cycle(self.plan_id)
        self.fetch.reset_mock()
        self.fixture.now.return_value = self.base.end

        result = capture_provider_cycle(self.plan_id)

        self.assertEqual(result["status"], "sealed")
        self.fetch.assert_not_called()
        self.assertEqual(
            self.fixture.row()["provider_collection"]["status"],
            "sealed",
        )

    def test_window_ending_during_fetch_does_not_append(self):
        self.prepare_capture()
        self.fixture.now.return_value = (
            self.base.end - timedelta(seconds=1)
        )

        def finish_at_end(plan, directory):
            saved = self.fake_capture(plan, directory)
            self.fixture.now.return_value = self.base.end
            return saved

        self.fetch.side_effect = finish_at_end
        result = capture_provider_cycle(self.plan_id)

        self.assertEqual(
            result["status"],
            "window_ended_during_capture",
        )
        self.assertEqual(self.count(), 0)

    def test_authority_rechecked_after_fetch(self):
        self.prepare_capture()
        self.authorize.side_effect = [
            None,
            None,
            PermissionError("authority revoked"),
        ]

        with self.assertRaises(PermissionError):
            capture_provider_cycle(self.plan_id)

        self.fetch.assert_called_once()
        self.assertEqual(self.count(), 0)


if __name__ == "__main__":
    unittest.main()
