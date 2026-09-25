"""Crypto batch tests with mocked requests and temporary storage."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from app.capital import quote_provenance_crypto_batch as batch
from app.capital.quote_provenance_store import load_quote_provenance


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
OBSERVED = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
DELTA = OBSERVED - EPOCH
SECONDS = DELTA.days * 86400 + DELTA.seconds
CAPTURED = datetime(2026, 9, 25, 12, 0, 5, tzinfo=timezone.utc)


def assets():
    return [
        {
            "id": 2,
            "symbol": "BTC",
            "asset_type": "crypto",
            "provider_id": "bitcoin",
        },
        {
            "id": 7,
            "symbol": "ETH",
            "asset_type": "crypto",
            "provider_id": "ethereum",
        },
    ]


def quotes():
    return {
        symbol: {
            "symbol": symbol,
            "price_usd": "100",
            "source": "coingecko_snapshot",
            "quote_timestamp": SECONDS,
        }
        for symbol in ("BTC", "ETH")
    }


class CryptoBatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "records"

        self.fetchers = ModuleType("app.watchlist_quotes")
        self.fetchers.CRYPTO_PROVIDER_IDS = {
            "BTC": "bitcoin",
            "ETH": "ethereum",
        }
        self.fetchers._fetch_crypto_quotes = Mock(return_value=quotes())

        modules = patch.dict(
            "sys.modules", {"app.watchlist_quotes": self.fetchers}
        )
        clock = patch.object(batch, "datetime")
        modules.start()
        self.clock = clock.start()
        self.clock.now.return_value = CAPTURED
        self.addCleanup(modules.stop)
        self.addCleanup(clock.stop)

    def capture(self, selected=None):
        return batch.capture_crypto_batch(
            assets=assets() if selected is None else selected,
            directory=self.root,
        )

    def test_multiple_assets_use_one_request_and_share_capture_time(self):
        result = self.capture()
        self.fetchers._fetch_crypto_quotes.assert_called_once_with(
            ["BTC", "ETH"]
        )
        self.assertEqual(result["status"], "captured")
        self.assertEqual(len(result["outcomes"]), 2)

        for outcome in result["outcomes"]:
            saved = load_quote_provenance(
                directory=self.root,
                record_id=outcome["record_id"],
            )
            self.assertEqual(saved["captured_at"], CAPTURED.isoformat())
            self.assertEqual(saved["provider_observed_at"], OBSERVED.isoformat())

    def test_capture_clock_is_sampled_after_request(self):
        events = []

        def fetch(symbols):
            events.append("fetch")
            return quotes()

        def now(*args):
            events.append("clock")
            return CAPTURED

        self.fetchers._fetch_crypto_quotes.side_effect = fetch
        self.clock.now.side_effect = now
        self.capture()
        self.assertEqual(events, ["fetch", "clock"])

    def test_empty_batch_makes_no_request_or_writes(self):
        result = self.capture([])
        self.assertEqual(result["status"], "empty")
        self.assertFalse(result["request_attempted"])
        self.assertEqual(result["outcomes"], [])
        self.fetchers._fetch_crypto_quotes.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_identity_mismatch_rejects_whole_batch_before_request(self):
        selected = assets()
        selected[1]["provider_id"] = "wrong-ethereum"
        with self.assertRaisesRegex(ValueError, "identities differ"):
            self.capture(selected)
        self.fetchers._fetch_crypto_quotes.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_duplicate_ids_and_symbols_are_rejected(self):
        for field, value in (("id", 2), ("symbol", "BTC")):
            with self.subTest(field=field):
                selected = assets()
                selected[1][field] = value
                with self.assertRaises(ValueError):
                    self.capture(selected)
        self.fetchers._fetch_crypto_quotes.assert_not_called()

    def test_stock_asset_is_rejected(self):
        selected = assets()
        selected[0]["asset_type"] = "stock"
        with self.assertRaisesRegex(ValueError, "crypto assets only"):
            self.capture(selected)
        self.fetchers._fetch_crypto_quotes.assert_not_called()

    def test_missing_quote_keeps_successful_asset(self):
        del self.fetchers._fetch_crypto_quotes.return_value["ETH"]
        result = self.capture()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["outcomes"][0]["status"], "captured")
        self.assertEqual(
            result["outcomes"][1]["reason"], "quote_missing_or_invalid"
        )
        self.assertEqual(len(list(self.root.glob("*.json"))), 1)

    def test_invalid_quote_does_not_prevent_other_asset_capture(self):
        self.fetchers._fetch_crypto_quotes.return_value["BTC"][
            "price_usd"
        ] = "NaN"
        result = self.capture()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["outcomes"][0]["status"], "failed")
        self.assertEqual(result["outcomes"][1]["status"], "captured")

    def test_unexpected_source_is_rejected(self):
        for quote in self.fetchers._fetch_crypto_quotes.return_value.values():
            quote["source"] = "live"
        result = self.capture()
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.root.exists())

    def test_missing_timestamp_is_preserved_without_fabrication(self):
        del self.fetchers._fetch_crypto_quotes.return_value["BTC"][
            "quote_timestamp"
        ]
        result = self.capture()
        outcome = result["outcomes"][0]
        self.assertEqual(outcome["status"], "captured")
        self.assertEqual(outcome["provider_time_status"], "missing")
        self.assertIsNone(outcome["provider_observed_at"])

    def test_future_timestamp_is_rejected_per_asset(self):
        self.fetchers._fetch_crypto_quotes.return_value["BTC"][
            "quote_timestamp"
        ] = SECONDS + 6
        result = self.capture()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(
            result["outcomes"][0]["reason"], "quote_missing_or_invalid"
        )

    def test_provider_error_is_sanitized_for_every_asset(self):
        self.fetchers._fetch_crypto_quotes.side_effect = RuntimeError(
            "https://example.invalid/?token=secret"
        )
        result = self.capture()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["request_attempted"])
        self.assertEqual(len(result["outcomes"]), 2)
        self.assertNotIn("secret", str(result))
        self.assertNotIn("example.invalid", str(result))
        self.assertFalse(self.root.exists())

    def test_malformed_response_is_reported_without_writing(self):
        self.fetchers._fetch_crypto_quotes.return_value = []
        result = self.capture()
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.root.exists())

    def test_storage_failure_is_reported_without_claiming_rollback(self):
        with patch.object(
            batch,
            "save_quote_provenance",
            side_effect=OSError("private storage detail"),
        ):
            result = self.capture()

        self.assertEqual(result["status"], "failed")
        for outcome in result["outcomes"]:
            self.assertEqual(
                outcome["reason"], "storage_failed_record_may_exist"
            )
        self.assertNotIn("private storage detail", str(result))

    def test_batch_order_is_stable_and_inputs_are_preserved(self):
        selected = list(reversed(assets()))
        response = self.fetchers._fetch_crypto_quotes.return_value
        original = deepcopy((selected, response))
        result = self.capture(selected)

        self.assertEqual((selected, response), original)
        self.assertEqual(
            [row["asset_id"] for row in result["outcomes"]], [2, 7]
        )

    def test_extra_response_assets_are_not_persisted(self):
        self.fetchers._fetch_crypto_quotes.return_value["SOL"] = {
            "symbol": "SOL",
            "price_usd": "100",
            "source": "coingecko_snapshot",
            "quote_timestamp": SECONDS,
        }
        result = self.capture()
        self.assertEqual(len(result["outcomes"]), 2)
        self.assertEqual(len(list(self.root.glob("*.json"))), 2)

    def test_keyboard_interrupt_is_not_hidden(self):
        self.fetchers._fetch_crypto_quotes.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.capture()


if __name__ == "__main__":
    unittest.main()
