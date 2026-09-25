"""Coverage tests using temporary provenance records only."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.capital.quote_provenance import make_quote_provenance
from app.capital.quote_provenance_coverage import (
    get_quote_provenance_coverage,
)
from app.capital.quote_provenance_store import save_quote_provenance


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
OBSERVED = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
DELTA = OBSERVED - EPOCH
SECONDS = DELTA.days * 86400 + DELTA.seconds
MEASURED = "2026-09-25T12:01:00+00:00"


def requested():
    return [{
        "asset_id": 2,
        "symbol": "BTC",
        "asset_type": "crypto",
        "provider": "CoinGecko REST",
    }]


class ProvenanceCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "records"

    def save(self, **changes):
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
        return save_quote_provenance(
            directory=self.root,
            record=make_quote_provenance(**arguments),
        )

    def coverage(self, **changes):
        arguments = {
            "directory": self.root,
            "requested_assets": requested(),
            "measured_at": MEASURED,
            "maximum_provider_age": timedelta(minutes=20),
            "maximum_capture_age": timedelta(minutes=2),
        }
        arguments.update(changes)
        return get_quote_provenance_coverage(**arguments)

    def test_missing_directory_reports_missing_without_creating_it(self):
        result = self.coverage()
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["assets"][0]["status"], "missing")
        self.assertEqual(result["verified_record_count"], 0)
        self.assertFalse(self.root.exists())

    def test_recent_record_is_eligible(self):
        saved = self.save()
        result = self.coverage()
        self.assertEqual(result["status"], "eligible")
        self.assertEqual(result["assets"][0]["record_id"], saved["record_id"])
        self.assertEqual(result["assets"][0]["status"], "eligible")
        self.assertEqual(result["verified_record_count"], 1)

    def test_missing_requested_asset_makes_coverage_incomplete(self):
        self.save()
        assets = requested() + [{
            "asset_id": 7,
            "symbol": "ETH",
            "asset_type": "crypto",
            "provider": "CoinGecko REST",
        }]
        result = self.coverage(requested_assets=assets)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["requested_count"], 2)
        self.assertEqual(result["assets"][1]["status"], "missing")

    def test_latest_capture_is_selected(self):
        self.save()
        newest = self.save(captured_at="2026-09-25T12:00:30+00:00")
        result = self.coverage()
        self.assertEqual(
            result["assets"][0]["record_id"], newest["record_id"]
        )

    def test_latest_missing_timestamp_does_not_fall_back(self):
        self.save()
        newest = self.save(
            captured_at="2026-09-25T12:00:30+00:00",
            provider_timestamp=None,
        )
        row = self.coverage()["assets"][0]
        self.assertEqual(row["record_id"], newest["record_id"])
        self.assertEqual(row["status"], "ineligible")
        self.assertEqual(
            row["assessment"]["reasons"], ["provider_timestamp_missing"]
        )

    def test_old_provider_quote_remains_ineligible(self):
        self.save(provider_timestamp=SECONDS - 3600)
        row = self.coverage()["assets"][0]
        self.assertEqual(row["status"], "ineligible")
        self.assertIn("provider_quote_too_old", row["assessment"]["reasons"])

    def test_future_capture_is_excluded(self):
        first = self.save()
        self.save(captured_at="2026-09-25T12:02:00+00:00")
        result = self.coverage()
        self.assertEqual(result["future_capture_count"], 1)
        self.assertEqual(result["verified_record_count"], 2)
        self.assertEqual(
            result["assets"][0]["record_id"], first["record_id"]
        )

    def test_only_future_capture_means_missing(self):
        self.save(captured_at="2026-09-25T12:02:00+00:00")
        result = self.coverage()
        self.assertEqual(result["assets"][0]["status"], "missing")
        self.assertEqual(result["future_capture_count"], 1)

    def test_conflicting_latest_records_are_ambiguous(self):
        self.save(price_usd="100")
        self.save(price_usd="101")
        row = self.coverage()["assets"][0]
        self.assertEqual(row["status"], "ambiguous")
        self.assertIsNone(row["record_id"])
        self.assertIsNone(row["assessment"])

    def test_identical_retry_is_not_ambiguous(self):
        self.save()
        self.save()
        result = self.coverage()
        self.assertEqual(result["verified_record_count"], 1)
        self.assertEqual(result["status"], "eligible")

    def test_corrupt_record_fails_closed(self):
        saved = self.save()
        Path(saved["path"]).write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            self.coverage()

    def test_unrelated_corrupt_record_is_not_silently_ignored(self):
        self.save()
        unrelated = self.save(asset_id=7, symbol="ETH")
        Path(unrelated["path"]).write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            self.coverage()

    def test_unexpected_json_filename_is_rejected(self):
        self.root.mkdir()
        (self.root / "unexpected.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "filename"):
            self.coverage()

    def test_saved_identity_mismatch_is_rejected(self):
        self.save(symbol="ETH")
        with self.assertRaisesRegex(ValueError, "identity differs"):
            self.coverage()

    def test_duplicate_requests_are_rejected(self):
        assets = requested()
        assets.append(deepcopy(assets[0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.coverage(requested_assets=assets)

    def test_invalid_requests_and_age_limits_are_rejected(self):
        for field, value in (
            ("asset_id", True),
            ("symbol", ""),
            ("provider", "unknown"),
            ("asset_type", "stock"),
        ):
            with self.subTest(field=field):
                assets = requested()
                assets[0][field] = value
                with self.assertRaises(ValueError):
                    self.coverage(requested_assets=assets)

        for field in ("maximum_provider_age", "maximum_capture_age"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self.coverage(**{field: timedelta(0)})

    def test_empty_request_does_not_claim_eligible_coverage(self):
        result = self.coverage(requested_assets=[])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["assets"], [])

    def test_input_and_saved_files_are_not_modified(self):
        saved = self.save()
        path = Path(saved["path"])
        before = path.read_bytes()
        assets = requested()
        original = deepcopy(assets)

        first = self.coverage(requested_assets=assets)
        second = self.coverage(requested_assets=assets)

        self.assertEqual(first, second)
        self.assertEqual(assets, original)
        self.assertEqual(path.read_bytes(), before)

    def test_no_execution_or_database_authority(self):
        self.save()
        result = self.coverage()
        for field in (
            "database_writes",
            "execution_authorized",
            "live_capital_authorized",
        ):
            self.assertFalse(result[field])


if __name__ == "__main__":
    unittest.main()
