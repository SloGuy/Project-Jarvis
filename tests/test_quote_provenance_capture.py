"""Capture tests with mocked providers and temporary storage only."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from app.capital.quote_provenance_capture import capture_quote_provenance
from app.capital.quote_provenance_store import load_quote_provenance


OBSERVED = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
DELTA = OBSERVED - EPOCH
SECONDS = DELTA.days * 86400 + DELTA.seconds
CAPTURED = datetime(2026, 9, 25, 12, 0, 5, tzinfo=timezone.utc)


def stock_asset():
    return {
        "id": 212,
        "symbol": "CTVA",
        "asset_type": "stock",
        "provider_id": None,
    }


def crypto_asset():
    return {
        "id": 2,
        "symbol": "BTC",
        "asset_type": "crypto",
        "provider_id": "bitcoin",
    }


class ProvenanceCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "records"

        self.fetchers = ModuleType("app.watchlist_quotes")
        self.fetchers.CRYPTO_PROVIDER_IDS = {"BTC": "bitcoin"}
        self.fetchers._fetch_stock_quote = Mock(return_value={
            "symbol": "CTVA",
            "price_usd": "90.25",
            "source": "finnhub_snapshot",
            "quote_timestamp": SECONDS,
        })
        self.fetchers._fetch_crypto_quotes = Mock(return_value={
            "BTC": {
                "symbol": "BTC",
                "price_usd": "100.25",
                "source": "coingecko_snapshot",
                "quote_timestamp": SECONDS,
            },
        })

        modules = patch.dict(
            "sys.modules", {"app.watchlist_quotes": self.fetchers}
        )
        clock = patch(
            "app.capital.quote_provenance_capture.datetime"
        )
        modules.start()
        self.clock = clock.start()
        self.clock.now.return_value = CAPTURED
        self.addCleanup(modules.stop)
        self.addCleanup(clock.stop)

    def capture(self, asset=None, **changes):
        arguments = {
            "asset": stock_asset() if asset is None else asset,
            "directory": self.root,
            "finnhub_api_key": "test-key",
        }
        arguments.update(changes)
        return capture_quote_provenance(**arguments)

    def load(self, result):
        return load_quote_provenance(
            directory=self.root,
            record_id=result["record_id"],
        )

    def test_stock_capture_persists_provider_and_local_times(self):
        result = self.capture()
        saved = self.load(result)
        self.fetchers._fetch_stock_quote.assert_called_once_with(
            "CTVA", "test-key"
        )
        self.fetchers._fetch_crypto_quotes.assert_not_called()
        self.assertEqual(saved["provider_observed_at"], OBSERVED.isoformat())
        self.assertEqual(saved["captured_at"], CAPTURED.isoformat())
        self.assertEqual(saved["provider"], "Finnhub REST")
        self.assertEqual(saved["asset_id"], 212)

    def test_crypto_capture_checks_identity_and_persists(self):
        result = self.capture(crypto_asset(), finnhub_api_key=None)
        saved = self.load(result)
        self.fetchers._fetch_crypto_quotes.assert_called_once_with(["BTC"])
        self.fetchers._fetch_stock_quote.assert_not_called()
        self.assertEqual(saved["provider"], "CoinGecko REST")
        self.assertEqual(saved["provider_observed_at"], OBSERVED.isoformat())

    def test_capture_clock_is_sampled_after_fetch_returns(self):
        events = []
        original = self.fetchers._fetch_stock_quote.return_value

        def fetch(*args):
            events.append("fetch")
            return original

        def now(*args):
            events.append("clock")
            return CAPTURED

        self.fetchers._fetch_stock_quote.side_effect = fetch
        self.clock.now.side_effect = now
        self.capture()
        self.assertEqual(events, ["fetch", "clock"])

    def test_missing_provider_timestamp_stays_missing(self):
        del self.fetchers._fetch_stock_quote.return_value["quote_timestamp"]
        result = self.capture()
        saved = self.load(result)
        self.assertIsNone(saved["provider_observed_at"])
        self.assertEqual(saved["provider_time_status"], "missing")

    def test_missing_crypto_timestamp_stays_missing(self):
        del self.fetchers._fetch_crypto_quotes.return_value["BTC"][
            "quote_timestamp"
        ]
        saved = self.load(self.capture(crypto_asset()))
        self.assertIsNone(saved["provider_observed_at"])

    def test_missing_credentials_prevent_stock_request(self):
        for key in (None, "", " ", 1):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.capture(finnhub_api_key=key)
        self.fetchers._fetch_stock_quote.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_crypto_id_mismatch_prevents_request(self):
        asset = crypto_asset()
        asset["provider_id"] = "another-bitcoin"
        with self.assertRaisesRegex(ValueError, "identities differ"):
            self.capture(asset)
        self.fetchers._fetch_crypto_quotes.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_unknown_crypto_symbol_prevents_request(self):
        asset = crypto_asset()
        asset["symbol"] = "UNKNOWN"
        with self.assertRaises(ValueError):
            self.capture(asset)
        self.fetchers._fetch_crypto_quotes.assert_not_called()

    def test_missing_crypto_response_does_not_write(self):
        self.fetchers._fetch_crypto_quotes.return_value = {}
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.capture(crypto_asset())
        self.assertFalse(self.root.exists())

    def test_unexpected_sources_are_rejected(self):
        self.fetchers._fetch_stock_quote.return_value["source"] = "live"
        with self.assertRaisesRegex(ValueError, "quote source"):
            self.capture()

        self.fetchers._fetch_crypto_quotes.return_value["BTC"]["source"] = "live"
        with self.assertRaisesRegex(ValueError, "quote source"):
            self.capture(crypto_asset())
        self.assertFalse(self.root.exists())

    def test_mismatched_response_symbol_is_rejected(self):
        self.fetchers._fetch_stock_quote.return_value["symbol"] = "SPY"
        with self.assertRaisesRegex(ValueError, "symbol does not match"):
            self.capture()
        self.assertFalse(self.root.exists())

    def test_future_timestamp_is_rejected_without_writing(self):
        self.fetchers._fetch_stock_quote.return_value["quote_timestamp"] = (
            SECONDS + 6
        )
        with self.assertRaisesRegex(ValueError, "after local capture"):
            self.capture()
        self.assertFalse(self.root.exists())

    def test_provider_failure_propagates_without_writing(self):
        self.fetchers._fetch_stock_quote.side_effect = RuntimeError(
            "provider unavailable"
        )
        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            self.capture()
        self.assertFalse(self.root.exists())

    def test_invalid_asset_identity_prevents_requests(self):
        for field, value in (
            ("id", True),
            ("id", 0),
            ("symbol", ""),
            ("asset_type", "unsupported"),
        ):
            with self.subTest(field=field):
                asset = stock_asset()
                asset[field] = value
                with self.assertRaises(ValueError):
                    self.capture(asset)
        self.fetchers._fetch_stock_quote.assert_not_called()
        self.fetchers._fetch_crypto_quotes.assert_not_called()

    def test_input_asset_and_response_are_preserved(self):
        asset = stock_asset()
        quote = self.fetchers._fetch_stock_quote.return_value
        original = deepcopy((asset, quote))
        self.capture(asset)
        self.assertEqual((asset, quote), original)

    def test_output_has_no_credentials_or_trading_authority(self):
        result = self.capture()
        self.assertNotIn("test-key", str(result))
        self.assertNotIn("test-key", str(self.load(result)))
        self.assertFalse(result["trading_state_writes"])
        self.assertFalse(result["market_quote_freshness_verified"])


if __name__ == "__main__":
    unittest.main()
