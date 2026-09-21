"""Tests for historical diagnostic quote selection."""

from copy import deepcopy
from datetime import timedelta
import unittest

from app.capital.portfolio_historical_quotes import select_historical_quote


def observation(**changes):
    row = {
        "id": 1,
        "asset_id": 7,
        "provider": "test-provider",
        "observed_at": "2026-09-20T11:50:00+00:00",
        "price_usd": "100",
    }
    row.update(changes)
    return row


def select_quote(**changes):
    arguments = {
        "observations": [observation()],
        "asset_id": 7,
        "provider": "test-provider",
        "measured_at": "2026-09-20T12:00:00+00:00",
        "maximum_age": timedelta(minutes=20),
    }
    arguments.update(changes)
    return select_historical_quote(**arguments)


class HistoricalQuoteTests(unittest.TestCase):
    def test_fresh_quote_is_usable(self):
        result = select_quote()
        self.assertEqual(result["status"], "usable")
        self.assertEqual(result["price_usd"], "100")
        self.assertEqual(result["observation_id"], 1)
        self.assertEqual(result["age_seconds"], 600)

    def test_latest_matching_quote_is_selected(self):
        result = select_quote(observations=[
            observation(
                id=2,
                observed_at="2026-09-20T11:55:00+00:00",
                price_usd="102",
            ),
            observation(),
        ])
        self.assertEqual(result["observation_id"], 2)
        self.assertEqual(result["price_usd"], "102")

    def test_future_quote_is_excluded(self):
        result = select_quote(observations=[
            observation(),
            observation(
                id=2,
                observed_at="2026-09-20T12:00:01+00:00",
                price_usd="999",
            ),
        ])
        self.assertEqual(result["observation_id"], 1)
        self.assertEqual(result["price_usd"], "100")

    def test_only_future_quotes_means_missing(self):
        result = select_quote(observations=[
            observation(observed_at="2026-09-20T12:01:00+00:00"),
        ])
        self.assertEqual(result["status"], "missing")
        self.assertIsNone(result["price_usd"])
        self.assertIsNone(result["observation_id"])

    def test_quote_at_measurement_time_is_usable(self):
        result = select_quote(observations=[
            observation(observed_at="2026-09-20T12:00:00+00:00"),
        ])
        self.assertEqual(result["status"], "usable")
        self.assertEqual(result["age_seconds"], 0)

    def test_maximum_age_boundary_is_inclusive(self):
        result = select_quote(observations=[
            observation(observed_at="2026-09-20T11:40:00+00:00"),
        ])
        self.assertEqual(result["status"], "usable")
        self.assertEqual(result["age_seconds"], 1200)

    def test_quote_just_beyond_age_limit_is_stale(self):
        result = select_quote(observations=[
            observation(
                observed_at="2026-09-20T11:39:59.999999+00:00"
            ),
        ])
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["price_usd"], "100")
        self.assertGreater(result["age_seconds"], 1200)

    def test_other_assets_and_providers_are_excluded(self):
        result = select_quote(observations=[
            observation(id=2, asset_id=8, price_usd="200"),
            observation(id=3, provider="other-provider", price_usd="300"),
        ])
        self.assertEqual(result["status"], "missing")
        self.assertIsNone(result["price_usd"])

    def test_invalid_latest_quote_does_not_fall_back(self):
        for price in ("0", "-1", "NaN", "Infinity", None, True, "bad"):
            with self.subTest(price=price):
                result = select_quote(observations=[
                    observation(),
                    observation(
                        id=2,
                        observed_at="2026-09-20T11:55:00+00:00",
                        price_usd=price,
                    ),
                ])
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(result["observation_id"], 2)
                self.assertIsNone(result["price_usd"])

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            select_quote(observations=[
                observation(),
                observation(observed_at="2026-09-20T11:55:00+00:00"),
            ])

    def test_equivalent_duplicate_timestamps_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            select_quote(observations=[
                observation(),
                observation(
                    id=2,
                    observed_at="2026-09-20T07:50:00-04:00",
                ),
            ])

    def test_timezone_offsets_are_normalized(self):
        result = select_quote(
            observations=[
                observation(observed_at="2026-09-20T07:50:00-04:00"),
            ],
            measured_at="2026-09-20T08:00:00-04:00",
        )
        self.assertEqual(result["status"], "usable")
        self.assertEqual(result["observed_at"], "2026-09-20T11:50:00+00:00")
        self.assertEqual(result["measured_at"], "2026-09-20T12:00:00+00:00")
        self.assertEqual(result["age_seconds"], 600)

    def test_naive_timestamps_are_rejected(self):
        with self.assertRaises(ValueError):
            select_quote(measured_at="2026-09-20T12:00:00")

        with self.assertRaises(ValueError):
            select_quote(observations=[
                observation(observed_at="2026-09-20T11:50:00"),
            ])

    def test_explicit_provider_and_positive_age_are_required(self):
        for provider in ("", " ", None, 7):
            with self.subTest(provider=provider):
                with self.assertRaises(ValueError):
                    select_quote(provider=provider)

        for age in (None, 1200, timedelta(0), timedelta(seconds=-1)):
            with self.subTest(age=age):
                with self.assertRaises(ValueError):
                    select_quote(maximum_age=age)

    def test_identifiers_must_be_positive_integers(self):
        for value in (True, None, 0, -1, "7", 7.0):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    select_quote(asset_id=value)
                with self.assertRaises(ValueError):
                    select_quote(observations=[observation(id=value)])

    def test_empty_input_is_missing_without_availability_claim(self):
        result = select_quote(observations=[])
        self.assertEqual(result["status"], "missing")
        self.assertIsNone(result["observed_at"])
        self.assertFalse(result["historical_availability_verified"])

    def test_input_is_preserved_and_result_is_deterministic(self):
        rows = [
            observation(
                id=2,
                observed_at="2026-09-20T11:55:00+00:00",
                price_usd="102",
            ),
            observation(),
        ]
        original = deepcopy(rows)

        result = select_quote(observations=rows)

        self.assertEqual(rows, original)
        self.assertEqual(result, select_quote(observations=list(reversed(rows))))
        self.assertFalse(result["historical_availability_verified"])


if __name__ == "__main__":
    unittest.main()
