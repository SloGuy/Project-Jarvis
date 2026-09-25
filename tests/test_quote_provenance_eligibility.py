"""Tests for provider-age, capture-age, and look-ahead checks."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from app.capital.quote_provenance import make_quote_provenance
from app.capital.quote_provenance_eligibility import assess_quote_provenance


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
PROVIDER_TIME = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
DELTA = PROVIDER_TIME - EPOCH
SECONDS = DELTA.days * 86400 + DELTA.seconds


def record(**changes):
    arguments = {
        "asset_id": 2,
        "symbol": "BTC",
        "asset_type": "crypto",
        "provider": "CoinGecko REST",
        "price_usd": "100",
        "provider_timestamp": SECONDS,
        "captured_at": "2026-09-25T12:00:05+00:00",
    }
    arguments.update(changes)
    return make_quote_provenance(**arguments)


def assess(value=None, **changes):
    arguments = {
        "record": record() if value is None else value,
        "measured_at": "2026-09-25T12:01:00+00:00",
        "maximum_provider_age": timedelta(minutes=20),
        "maximum_capture_age": timedelta(minutes=2),
    }
    arguments.update(changes)
    return assess_quote_provenance(**arguments)


class ProvenanceEligibilityTests(unittest.TestCase):
    def test_recent_provider_and_capture_times_are_eligible(self):
        result = assess()
        self.assertEqual(result["status"], "eligible")
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["provider_age_seconds"], 60)
        self.assertEqual(result["capture_age_seconds"], 55)

    def test_provider_age_boundary_is_inclusive(self):
        value = record(captured_at="2026-09-25T12:19:59+00:00")
        result = assess(
            value, measured_at="2026-09-25T12:20:00+00:00"
        )
        self.assertEqual(result["status"], "eligible")
        self.assertEqual(result["provider_age_seconds"], 1200)

    def test_provider_age_just_beyond_limit_is_ineligible(self):
        value = record(captured_at="2026-09-25T12:19:59+00:00")
        result = assess(
            value,
            measured_at="2026-09-25T12:20:00.000001+00:00",
        )
        self.assertEqual(result["status"], "ineligible")
        self.assertEqual(result["reasons"], ["provider_quote_too_old"])

    def test_capture_age_boundary_is_inclusive(self):
        result = assess(measured_at="2026-09-25T12:02:05+00:00")
        self.assertEqual(result["status"], "eligible")
        self.assertEqual(result["capture_age_seconds"], 120)

    def test_capture_age_just_beyond_limit_is_ineligible(self):
        result = assess(
            measured_at="2026-09-25T12:02:05.000001+00:00"
        )
        self.assertEqual(result["reasons"], ["capture_too_old"])

    def test_recent_capture_does_not_refresh_old_provider_quote(self):
        value = record(
            asset_id=212,
            symbol="CTVA",
            asset_type="stock",
            provider="Finnhub REST",
            captured_at="2026-09-25T13:34:00+00:00",
        )
        result = assess(
            value, measured_at="2026-09-25T13:34:01+00:00"
        )
        self.assertEqual(result["status"], "ineligible")
        self.assertEqual(result["capture_age_seconds"], 1)
        self.assertEqual(result["reasons"], ["provider_quote_too_old"])

    def test_missing_provider_time_is_ineligible(self):
        result = assess(record(provider_timestamp=None))
        self.assertEqual(result["status"], "ineligible")
        self.assertEqual(result["reasons"], ["provider_timestamp_missing"])
        self.assertIsNone(result["provider_age_seconds"])

    def test_capture_after_measurement_is_ineligible(self):
        result = assess(measured_at="2026-09-25T12:00:01+00:00")
        self.assertEqual(result["status"], "ineligible")
        self.assertEqual(result["reasons"], ["captured_after_measurement"])
        self.assertEqual(result["capture_age_seconds"], -4)

    def test_provider_and_capture_after_measurement_are_both_reported(self):
        result = assess(measured_at="2026-09-25T11:59:59+00:00")
        self.assertEqual(
            result["reasons"],
            ["captured_after_measurement", "provider_time_after_measurement"],
        )

    def test_both_age_failures_are_reported(self):
        result = assess(measured_at="2026-09-25T13:00:00+00:00")
        self.assertEqual(
            result["reasons"],
            ["capture_too_old", "provider_quote_too_old"],
        )

    def test_invalid_thresholds_are_rejected(self):
        for field in ("maximum_provider_age", "maximum_capture_age"):
            for value in (None, 120, timedelta(0), timedelta(seconds=-1)):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        assess(**{field: value})

    def test_naive_measurement_time_is_rejected(self):
        with self.assertRaises(ValueError):
            assess(measured_at="2026-09-25T12:01:00")

    def test_measurement_timezone_is_normalized(self):
        result = assess(measured_at="2026-09-25T08:01:00-04:00")
        self.assertEqual(
            result["measured_at"], "2026-09-25T12:01:00+00:00"
        )
        self.assertEqual(result["status"], "eligible")

    def test_modified_derived_fields_are_rejected(self):
        for field, value in (
            ("provider_observed_at", "2026-09-25T12:00:01+00:00"),
            ("provider_time_status", "missing"),
            ("schema_version", True),
            ("market_quote_freshness_verified", True),
        ):
            with self.subTest(field=field):
                altered = record()
                altered[field] = value
                with self.assertRaises(ValueError):
                    assess(altered)

    def test_missing_and_extra_fields_are_rejected(self):
        missing = record()
        del missing["asset_id"]
        with self.assertRaises(ValueError):
            assess(missing)

        extra = record()
        extra["approved"] = True
        with self.assertRaises(ValueError):
            assess(extra)

    def test_input_is_preserved_and_result_is_deterministic(self):
        value = record()
        original = deepcopy(value)
        first = assess(value)
        self.assertEqual(value, original)
        self.assertEqual(first, assess(value))

    def test_eligibility_does_not_grant_broader_verification_or_authority(self):
        result = assess()
        self.assertEqual(result["scope"], "timestamp_eligibility_only")
        for field in (
            "exchange_session_verified",
            "provider_authenticity_verified",
            "historical_completeness_verified",
            "execution_authorized",
            "live_capital_authorized",
        ):
            self.assertFalse(result[field])


if __name__ == "__main__":
    unittest.main()
