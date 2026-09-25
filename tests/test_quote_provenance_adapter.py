"""Tests for REST response identity and timestamp preservation."""

from copy import deepcopy
from datetime import datetime, timezone
import unittest

from app.capital.quote_provenance_adapter import (
    coingecko_rest_provenance,
    finnhub_rest_provenance,
)


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
OBSERVED = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
DELTA = OBSERVED - EPOCH
SECONDS = DELTA.days * 86400 + DELTA.seconds
CAPTURED = "2026-09-25T12:00:05+00:00"


def stock_quote():
    return {
        "symbol": "SPY",
        "available": True,
        "price_usd": "100.25",
        "quote_timestamp": SECONDS,
    }


def crypto_quote():
    return {
        "id": "bitcoin",
        "symbol": "BTC",
        "available": True,
        "price_usd": "100.25",
        "last_updated_at": SECONDS,
    }


def stock(quote=None, **changes):
    arguments = {
        "asset_id": 1,
        "symbol": "SPY",
        "quote": stock_quote() if quote is None else quote,
        "captured_at": CAPTURED,
    }
    arguments.update(changes)
    return finnhub_rest_provenance(**arguments)


def crypto(quote=None, **changes):
    arguments = {
        "asset_id": 2,
        "symbol": "BTC",
        "provider_id": "bitcoin",
        "quote": crypto_quote() if quote is None else quote,
        "captured_at": CAPTURED,
    }
    arguments.update(changes)
    return coingecko_rest_provenance(**arguments)


class ProvenanceAdapterTests(unittest.TestCase):
    def test_stock_preserves_provider_time(self):
        result = stock()
        self.assertEqual(result["provider"], "Finnhub REST")
        self.assertEqual(result["asset_type"], "stock")
        self.assertEqual(result["provider_timestamp_raw"], SECONDS)
        self.assertEqual(result["provider_observed_at"], OBSERVED.isoformat())
        self.assertEqual(result["captured_at"], CAPTURED)

    def test_crypto_preserves_provider_time(self):
        result = crypto()
        self.assertEqual(result["provider"], "CoinGecko REST")
        self.assertEqual(result["asset_type"], "crypto")
        self.assertEqual(result["provider_timestamp_raw"], SECONDS)
        self.assertEqual(result["provider_observed_at"], OBSERVED.isoformat())

    def test_missing_stock_timestamp_never_uses_alternative_fields(self):
        quote = stock_quote()
        del quote["quote_timestamp"]
        quote["observed_at"] = CAPTURED
        quote["received_at"] = CAPTURED
        quote["checked_at"] = CAPTURED
        result = stock(quote)
        self.assertIsNone(result["provider_observed_at"])
        self.assertEqual(result["provider_time_status"], "missing")

    def test_missing_crypto_timestamp_never_uses_alternative_fields(self):
        quote = crypto_quote()
        del quote["last_updated_at"]
        quote["observed_at"] = CAPTURED
        quote["received_at"] = CAPTURED
        quote["checked_at"] = CAPTURED
        result = crypto(quote)
        self.assertIsNone(result["provider_observed_at"])
        self.assertEqual(result["provider_time_status"], "missing")

    def test_explicit_none_timestamp_remains_missing(self):
        first = stock_quote()
        first["quote_timestamp"] = None
        second = crypto_quote()
        second["last_updated_at"] = None
        for result in (stock(first), crypto(second)):
            self.assertIsNone(result["provider_timestamp_raw"])
            self.assertIsNone(result["provider_observed_at"])

    def test_availability_must_be_exactly_true(self):
        for value in (False, None, 1, "true"):
            with self.subTest(value=value):
                first = stock_quote()
                first["available"] = value
                second = crypto_quote()
                second["available"] = value
                with self.assertRaises(ValueError):
                    stock(first)
                with self.assertRaises(ValueError):
                    crypto(second)

    def test_wrong_symbol_is_rejected(self):
        first = stock_quote()
        first["symbol"] = "QQQ"
        second = crypto_quote()
        second["symbol"] = "ETH"
        with self.assertRaisesRegex(ValueError, "symbol does not match"):
            stock(first)
        with self.assertRaisesRegex(ValueError, "symbol does not match"):
            crypto(second)

    def test_missing_symbol_is_rejected(self):
        first = stock_quote()
        del first["symbol"]
        second = crypto_quote()
        del second["symbol"]
        with self.assertRaisesRegex(ValueError, "symbol is missing"):
            stock(first)
        with self.assertRaisesRegex(ValueError, "symbol is missing"):
            crypto(second)

    def test_symbol_whitespace_and_case_are_normalized(self):
        first = stock_quote()
        first["symbol"] = " spy "
        second = crypto_quote()
        second["symbol"] = " btc "
        self.assertEqual(stock(first, symbol=" spy ")["symbol"], "SPY")
        self.assertEqual(crypto(second, symbol=" btc ")["symbol"], "BTC")

    def test_wrong_coingecko_id_is_rejected(self):
        quote = crypto_quote()
        quote["id"] = "another-bitcoin"
        with self.assertRaisesRegex(ValueError, "CoinGecko ID"):
            crypto(quote)

    def test_missing_coingecko_id_is_rejected(self):
        quote = crypto_quote()
        del quote["id"]
        with self.assertRaisesRegex(ValueError, "CoinGecko ID"):
            crypto(quote)

    def test_invalid_expected_provider_id_is_rejected(self):
        for value in ("", " ", None, 1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    crypto(provider_id=value)

    def test_invalid_identity_arguments_are_rejected(self):
        for value in (True, 0, -1, "1", None):
            with self.subTest(asset_id=value):
                with self.assertRaises(ValueError):
                    stock(asset_id=value)
        for value in ("", " ", None, 1):
            with self.subTest(symbol=value):
                with self.assertRaises(ValueError):
                    stock(symbol=value)

    def test_invalid_price_and_timestamp_are_rejected(self):
        for field, value in (
            ("price_usd", "NaN"),
            ("price_usd", "0"),
            ("quote_timestamp", True),
            ("quote_timestamp", SECONDS + 6),
        ):
            with self.subTest(field=field, value=value):
                quote = stock_quote()
                quote[field] = value
                with self.assertRaises(ValueError):
                    stock(quote)

    def test_input_quotes_are_preserved(self):
        first = stock_quote()
        second = crypto_quote()
        original = deepcopy((first, second))
        stock(first)
        crypto(second)
        self.assertEqual((first, second), original)

    def test_capture_time_remains_separate_and_no_freshness_is_claimed(self):
        for result in (stock(), crypto()):
            self.assertNotEqual(
                result["provider_observed_at"], result["captured_at"]
            )
            self.assertEqual(
                result["capture_time_basis"], "local_response_processing"
            )
            self.assertFalse(result["market_quote_freshness_verified"])


if __name__ == "__main__":
    unittest.main()
