"""Integrity, identity, and timing tests for provenance receipts."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from app.capital.quote_provenance import make_quote_provenance
from app.capital.quote_provenance_store import save_quote_provenance
from app.capital.validation_plan import (
    BENCHMARK,
    plan_digest,
    seal_plan,
)
from app.capital.validation_quote_receipt import (
    make_validation_quote_receipt,
    verify_validation_quote_receipt,
)


class ValidationQuoteReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

        self.created = datetime(
            2026, 10, 6, 18, 0,
            tzinfo=timezone.utc,
        )
        self.bound = self.created + timedelta(minutes=5)
        self.start = self.created + timedelta(minutes=30)
        self.end = self.start + timedelta(hours=1)
        self.captured = self.start + timedelta(seconds=10)
        self.recorded = self.captured + timedelta(seconds=1)

        draft = {
            "schema_version": 1,
            "designation": "prospective_validation",
            "created_by": "isolated-receipt-test",
            "research": {
                "research_id": "research_test",
                "hypothesis_version": 1,
                "strategy_name": "mean_reversion_v2",
                "hypothesis": "Test receipt integrity.",
                "asset_universe": ["BTC"],
                "success_criteria": ["Test-only fixture."],
            },
            "strategy_version": "test",
            "asset_id": 1,
            "symbol": "BTC",
            "provider": "CoinGecko",
            "start": self.start.isoformat(),
            "end_exclusive": self.end.isoformat(),
            "fee_bps": "5",
            "slippage_bps": "5",
            "benchmark": BENCHMARK,
            "criteria": {
                "minimum_completed_trades": 30,
                "maximum_stale_tick_percent": "5",
                "maximum_unusable_regular_tick_percent": "10",
                "minimum_return_percent": "0",
                "minimum_excess_return_percent": "0",
                "maximum_drawdown_percent": "5",
            },
            "policy": {"test_fixture": True},
            "execution_manifest": {"test_fixture": True},
        }

        # Local contract fixture; never registered or used as research policy.
        self.envelope = seal_plan(draft, now=self.created)
        self.sha = self.envelope["sha256"]
        self.ident = self.save_quote()

    def save_quote(self, captured=None, asset_id=1, provider_time=True):
        captured = captured or self.captured
        record = make_quote_provenance(
            asset_id=asset_id,
            symbol="BTC",
            asset_type="crypto",
            provider="CoinGecko REST",
            price_usd="60000",
            provider_timestamp=(
                int(
                    (captured - timedelta(seconds=30)).timestamp()
                )
                if provider_time else None
            ),
            captured_at=captured,
        )
        return save_quote_provenance(
            directory=self.directory,
            record=record,
        )["record_id"]

    def make(self, **overrides):
        arguments = {
            "envelope": self.envelope,
            "expected_sha256": self.sha,
            "bound_at": self.bound.isoformat(),
            "recorded_at": self.recorded.isoformat(),
            "directory": self.directory,
            "record_id": self.ident,
        }
        arguments.update(overrides)
        return make_validation_quote_receipt(**arguments)

    def verify(self, receipt, **overrides):
        arguments = {
            "receipt": receipt,
            "envelope": self.envelope,
            "expected_sha256": self.sha,
            "expected_bound_at": self.bound.isoformat(),
        }
        arguments.update(overrides)
        return verify_validation_quote_receipt(**arguments)

    def test_valid_receipt(self):
        receipt = self.make()
        payload = self.verify(receipt)
        self.assertEqual(payload["record_id"], self.ident)
        self.assertEqual(payload["plan_sha256"], self.sha)

    def test_changed_plan_is_rejected(self):
        changed = deepcopy(self.envelope)
        changed["plan"]["fee_bps"] = "0"
        with self.assertRaises(ValueError):
            self.make(envelope=changed)

    def test_wrong_asset_is_rejected(self):
        ident = self.save_quote(asset_id=2)
        with self.assertRaisesRegex(ValueError, "identity"):
            self.make(record_id=ident)

    def test_binding_at_start_is_rejected(self):
        with self.assertRaises(ValueError):
            self.make(bound_at=self.start.isoformat())

    def test_binding_before_plan_creation_is_rejected(self):
        before = self.created - timedelta(seconds=1)
        with self.assertRaises(ValueError):
            self.make(bound_at=before.isoformat())

    def test_quote_captured_before_binding_is_rejected(self):
        before = self.bound - timedelta(seconds=1)
        ident = self.save_quote(captured=before)
        with self.assertRaises(ValueError):
            self.make(record_id=ident)

    def test_logging_before_capture_is_rejected(self):
        before = self.captured - timedelta(seconds=1)
        with self.assertRaises(ValueError):
            self.make(recorded_at=before.isoformat())

    def test_logging_at_end_is_rejected(self):
        with self.assertRaises(ValueError):
            self.make(recorded_at=self.end.isoformat())

    def test_warmup_receipt_is_allowed(self):
        captured = self.bound + timedelta(seconds=10)
        recorded = captured + timedelta(seconds=1)
        ident = self.save_quote(captured=captured)
        payload = self.verify(self.make(
            record_id=ident,
            recorded_at=recorded.isoformat(),
        ))
        self.assertLess(payload["recorded_at"], self.start.isoformat())

    def test_missing_provider_time_is_preserved(self):
        ident = self.save_quote(provider_time=False)
        payload = self.verify(self.make(record_id=ident))
        self.assertIsNone(payload["record"]["provider_observed_at"])
        self.assertEqual(
            payload["record"]["provider_time_status"],
            "missing",
        )

    def test_changed_receipt_hash_is_rejected(self):
        receipt = self.make()
        receipt["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.verify(receipt)

    def test_rehashed_record_id_change_is_rejected(self):
        receipt = self.make()
        receipt["payload"]["record_id"] = "0" * 64
        receipt["sha256"] = plan_digest(receipt["payload"])
        with self.assertRaisesRegex(ValueError, "record ID"):
            self.verify(receipt)

    def test_rehashed_derived_time_change_is_rejected(self):
        receipt = self.make()
        receipt["payload"]["record"]["provider_observed_at"] = (
            self.captured.isoformat()
        )
        receipt["sha256"] = plan_digest(receipt["payload"])
        with self.assertRaisesRegex(ValueError, "derived"):
            self.verify(receipt)

    def test_wrong_retained_binding_is_rejected(self):
        different = self.bound + timedelta(seconds=1)
        with self.assertRaisesRegex(ValueError, "binding"):
            self.verify(
                self.make(),
                expected_bound_at=different.isoformat(),
            )

    def test_verified_result_is_an_independent_copy(self):
        receipt = self.make()
        payload = self.verify(receipt)
        payload["record"]["price_usd"] = "1"
        self.assertEqual(
            receipt["payload"]["record"]["price_usd"],
            "60000",
        )


if __name__ == "__main__":
    unittest.main()
