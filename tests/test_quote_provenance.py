"""Tests for provider timestamps and separate local capture times."""

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from app.capital.quote_provenance import make_quote_provenance


# 2026-09-25 12:00:00 UTC, calculated using integer datetime arithmetic.
PROVIDER_TIME = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
SECONDS = (PROVIDER_TIME - EPOCH).days * 86400 + (
    PROVIDER_TIME - EPOCH
).seconds
CAPTURED = "2026-09-25T12:00:05+00:00"


def make(**changes):
    arguments = {
        "asset_id": 7,
        "symbol": "BTC",
        "asset_type": "crypto",
        "provider": "CoinGecko REST",
        "price_usd": "100.12345678",
        "provider_timestamp": SECONDS,
        "captured_at": CAPTURED,
    }
    arguments.update(changes)
    return make_quote_provenance(**arguments)


class QuoteProvenanceTests(unittest.TestCase):
    def test_coingecko_seconds_preserve_provider_and_capture_times(self):
        result = make()
        self.assertEqual(
            result["provider_observed_at"], PROVIDER_TIME.isoformat()
        )
        self.assertEqual(result["captured_at"], CAPTURED)
        self.assertEqual(result["provider_timestamp_raw"], SECONDS)
        self.assertEqual(result["provider_timestamp_unit"], "seconds")
        self.assertEqual(result["provider_time_status"], "reported")

    def test_finnhub_rest_uses_seconds(self):
        result = make(
            provider="Finnhub REST",
            asset_type="stock",
            symbol="SPY",
        )
        self.assertEqual(result["provider_timestamp_unit"], "seconds")
        self.assertEqual(
            result["provider_observed_at"], PROVIDER_TIME.isoformat()
        )

    def test_finnhub_websocket_preserves_milliseconds(self):
        result = make(
            provider="Finnhub WebSocket",
            asset_type="stock",
            symbol="SPY",
            provider_timestamp=SECONDS * 1000 + 123,
        )
        self.assertEqual(result["provider_timestamp_unit"], "milliseconds")
        self.assertEqual(
            result["provider_observed_at"],
            "2026-09-25T12:00:00.123000+00:00",
        )

    def test_missing_provider_timestamp_is_not_replaced(self):
        result = make(provider_timestamp=None)
        self.assertIsNone(result["provider_timestamp_raw"])
        self.assertIsNone(result["provider_observed_at"])
        self.assertEqual(result["provider_time_status"], "missing")
        self.assertEqual(result["captured_at"], CAPTURED)

    def test_old_timestamp_is_preserved_without_freshness_claim(self):
        result = make(provider_timestamp=SECONDS - 86400 * 20)
        self.assertEqual(
            result["provider_observed_at"], "2026-09-05T12:00:00+00:00"
        )
        self.assertFalse(result["market_quote_freshness_verified"])

    def test_future_provider_timestamp_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "after local capture"):
            make(provider_timestamp=SECONDS + 6)

    def test_provider_timestamp_equal_to_capture_is_allowed(self):
        result = make(provider_timestamp=SECONDS + 5)
        self.assertEqual(result["provider_observed_at"], CAPTURED)

    def test_invalid_provider_timestamps_are_rejected(self):
        for value in (True, False, 0, -1, "123", 123.0, float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    make(provider_timestamp=value)

    def test_out_of_range_timestamp_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "supported range"):
            make(provider_timestamp=10 ** 30)

    def test_invalid_prices_are_rejected(self):
        for value in (True, None, "0", "-1", "NaN", "Infinity", "bad"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    make(price_usd=value)

    def test_decimal_price_is_preserved(self):
        result = make(price_usd=Decimal("0.00000001"))
        self.assertEqual(Decimal(result["price_usd"]), Decimal("0.00000001"))

    def test_provider_asset_type_must_match(self):
        with self.assertRaisesRegex(ValueError, "Asset type"):
            make(asset_type="stock")
        with self.assertRaisesRegex(ValueError, "Asset type"):
            make(provider="Finnhub REST", asset_type="crypto")

    def test_unknown_provider_is_rejected(self):
        for provider in ("Finnhub", "CoinGecko", "", None):
            with self.subTest(provider=provider):
                with self.assertRaises(ValueError):
                    make(provider=provider)

    def test_capture_timestamp_requires_timezone(self):
        for value in ("2026-09-25T12:00:05", None, 123):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    make(captured_at=value)

    def test_capture_timezone_is_normalized(self):
        result = make(captured_at="2026-09-25T08:00:05-04:00")
        self.assertEqual(result["captured_at"], CAPTURED)

    def test_asset_id_and_symbol_are_validated(self):
        for value in (True, 0, -1, "7", 7.0, None):
            with self.subTest(asset_id=value):
                with self.assertRaises(ValueError):
                    make(asset_id=value)
        for value in ("", " ", None, 7):
            with self.subTest(symbol=value):
                with self.assertRaises(ValueError):
                    make(symbol=value)
        self.assertEqual(make(symbol=" btc ")["symbol"], "BTC")

    def test_output_is_deterministic_and_claims_are_limited(self):
        result = make()
        self.assertEqual(result, make())
        self.assertEqual(
            result["capture_time_basis"], "local_response_processing"
        )
        for field in (
            "market_quote_freshness_verified",
            "provider_authenticity_verified",
            "feed_completeness_verified",
        ):
            self.assertFalse(result[field])


if __name__ == "__main__":
    unittest.main()
