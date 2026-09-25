"""Temporary-directory tests for atomic quote provenance storage."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.capital.quote_provenance import make_quote_provenance
from app.capital import quote_provenance_store as store


def record():
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    observed = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
    delta = observed - epoch
    return make_quote_provenance(
        asset_id=7,
        symbol="BTC",
        asset_type="crypto",
        provider="CoinGecko REST",
        price_usd="100.25",
        provider_timestamp=delta.days * 86400 + delta.seconds,
        captured_at="2026-09-25T12:00:05+00:00",
    )


class ProvenanceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "provenance"

    def save(self, value=None):
        return store.save_quote_provenance(
            directory=self.root,
            record=record() if value is None else value,
        )

    def load(self, record_id):
        return store.load_quote_provenance(
            directory=self.root,
            record_id=record_id,
        )

    def test_round_trip_preserves_record(self):
        value = record()
        saved = self.save(value)
        self.assertEqual(self.load(saved["record_id"]), value)
        self.assertEqual(
            Path(saved["path"]),
            self.root / f'{saved["record_id"]}.json',
        )

    def test_identical_retry_preserves_existing_file(self):
        first = self.save()
        path = Path(first["path"])
        before = path.stat()
        contents = path.read_bytes()

        second = self.save()

        after = path.stat()
        self.assertEqual(first, second)
        self.assertEqual(contents, path.read_bytes())
        self.assertEqual(before.st_ino, after.st_ino)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
        self.assertEqual(len(list(self.root.glob("*.json"))), 1)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_distinct_capture_has_distinct_identity(self):
        first = self.save()
        value = record()
        value["captured_at"] = "2026-09-25T12:00:06+00:00"
        second = self.save(value)
        self.assertNotEqual(first["record_id"], second["record_id"])
        self.assertEqual(len(list(self.root.glob("*.json"))), 2)

    def test_corrupt_existing_file_is_never_overwritten(self):
        saved = self.save()
        path = Path(saved["path"])
        path.write_bytes(b"corrupt")

        with self.assertRaises(ValueError):
            self.save()

        self.assertEqual(path.read_bytes(), b"corrupt")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_changed_contents_fail_hash_verification(self):
        saved = self.save()
        changed = record()
        changed["price_usd"] = "999"
        Path(saved["path"]).write_text(
            json.dumps(changed, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.load(saved["record_id"])

    def test_modified_derived_fields_are_rejected_before_writing(self):
        for field, value in (
            ("provider_time_status", "missing"),
            ("provider_observed_at", None),
            ("market_quote_freshness_verified", True),
            ("schema_version", True),
        ):
            with self.subTest(field=field):
                altered = record()
                altered[field] = value
                with self.assertRaises(ValueError):
                    self.save(altered)
        self.assertFalse(self.root.exists())

    def test_missing_or_extra_fields_are_rejected(self):
        missing = record()
        del missing["asset_id"]
        with self.assertRaises(ValueError):
            self.save(missing)

        extra = record()
        extra["approval"] = True
        with self.assertRaises(ValueError):
            self.save(extra)

        self.assertFalse(self.root.exists())

    def test_invalid_record_ids_are_rejected(self):
        for identifier in (None, "", "../outside", "a" * 63, "A" * 64):
            with self.subTest(identifier=identifier):
                with self.assertRaises(ValueError):
                    self.load(identifier)

    def test_missing_record_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            self.load("a" * 64)

    def test_duplicate_json_fields_are_rejected(self):
        self.root.mkdir()
        raw = b'{"schema_version":1,"schema_version":1}'
        identifier = hashlib.sha256(raw).hexdigest()
        (self.root / f"{identifier}.json").write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "decode"):
            self.load(identifier)

    def test_noncanonical_encoding_is_rejected(self):
        saved = self.save()
        Path(saved["path"]).write_text(
            json.dumps(record(), indent=2),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.load(saved["record_id"])

    def test_publication_failure_leaves_no_record_or_temporary_file(self):
        with patch.object(store.os, "link", side_effect=OSError("link failed")):
            with self.assertRaisesRegex(OSError, "link failed"):
                self.save()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_file_sync_failure_prevents_publication(self):
        with patch.object(store.os, "fsync", side_effect=OSError("sync failed")):
            with self.assertRaisesRegex(OSError, "sync failed"):
                self.save()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_directory_sync_failure_is_reported_and_retry_recovers(self):
        with patch.object(
            store, "_sync_directory", side_effect=OSError("directory sync failed")
        ):
            with self.assertRaisesRegex(OSError, "directory sync failed"):
                self.save()

        # Publication may have happened before the durability error.
        # Retrying must verify the existing file without replacing it.
        saved = self.save()
        self.assertEqual(self.load(saved["record_id"]), record())
        self.assertEqual(len(list(self.root.glob("*.json"))), 1)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_concurrent_identical_writers_converge_on_one_file(self):
        # Pre-create the directory to isolate publication concurrency.
        self.root.mkdir()
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.save(), range(8)))

        self.assertEqual(len({row["record_id"] for row in results}), 1)
        self.assertEqual(len(list(self.root.glob("*.json"))), 1)
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        self.assertEqual(self.load(results[0]["record_id"]), record())

    def test_input_record_is_not_mutated(self):
        value = record()
        original = deepcopy(value)
        self.save(value)
        self.assertEqual(value, original)


if __name__ == "__main__":
    unittest.main()
