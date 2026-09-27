"""Checkpoint persistence tests using temporary files only."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.capital import portfolio_checkpoint_store as store


STAMP = "2026-09-27T09:00:00+00:00"


def checkpoint():
    return {
        "schema_version": 1,
        "methodology": "prospective_portfolio_evidence_snapshot_v1",
        "started_at": STAMP,
        "sampled_at": STAMP,
        "finished_at": STAMP,
        "resolved_portfolios": [{"portfolio_id": 1}],
        "audit_snapshot": {
            "sampled_at": STAMP,
            "installation": {"installation_id": "test-installation"},
            "visibility_snapshot": "100:110:105",
            "events": [{
                "before": {"cash": Decimal("123456789012.12345678")},
                "after": {"quantity": Decimal("0.123456789123")},
            }],
        },
        "valuation_inputs": {
            "snapshot_at": STAMP,
            "audit_installation_id": "test-installation",
            "audit_visibility_snapshot": "100:110:105",
        },
        "row_reconciliation": {"status": "row_images_match"},
        "accounting_effects": {"status": "amounts_match"},
        "valuations": {
            "portfolios": [],
            "age_seconds": 12.5,
        },
        "selected_quote_records": {},
        "limitations": ["Test payload; storage does not certify evidence."],
        "checkpoint_persisted": False,
        "historical_return_eligible": False,
        "database_writes": False,
        "execution_authorized": False,
        "allocation_authority": False,
        "live_capital_authority": False,
    }


class CheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.data = checkpoint()

    def save(self):
        return store.save_portfolio_checkpoint(
            directory=self.directory,
            checkpoint=self.data,
        )

    def load(self, record_id):
        return store.load_portfolio_checkpoint(
            directory=self.directory,
            record_id=record_id,
        )

    def publish_raw(self, raw):
        identifier = hashlib.sha256(raw).hexdigest()
        (self.directory / f"{identifier}.json").write_bytes(raw)
        return identifier

    def test_exact_decimal_round_trip(self):
        saved = self.save()
        loaded = self.load(saved["record_id"])
        self.assertEqual(loaded, self.data)
        event = loaded["audit_snapshot"]["events"][0]
        self.assertIsInstance(event["before"]["cash"], Decimal)
        self.assertEqual(
            event["before"]["cash"], Decimal("123456789012.12345678")
        )
        self.assertEqual(
            event["after"]["quantity"], Decimal("0.123456789123")
        )
        self.assertIsInstance(loaded["valuations"]["age_seconds"], float)

    def test_decimal_scale_and_zero_are_preserved(self):
        self.data["valuations"]["numbers"] = [
            Decimal("1.2300"), Decimal("0E-12"), Decimal("-0.00")
        ]
        loaded = self.load(self.save()["record_id"])
        self.assertEqual(
            [str(value) for value in loaded["valuations"]["numbers"]],
            ["1.2300", "0E-12", "-0.00"],
        )

    def test_identical_retry_does_not_replace_file(self):
        first = self.save()
        path = Path(first["path"])
        original = (path.stat().st_ino, path.read_bytes())
        second = self.save()
        self.assertEqual(first, second)
        self.assertEqual((path.stat().st_ino, path.read_bytes()), original)
        self.assertEqual(len(list(self.directory.glob("*.json"))), 1)

    def test_changed_payload_has_different_identity(self):
        first = self.save()
        self.data["valuations"]["age_seconds"] = 13.5
        second = self.save()
        self.assertNotEqual(first["record_id"], second["record_id"])

    def test_persistence_receipt_does_not_mutate_capture(self):
        original = deepcopy(self.data)
        saved = self.save()
        self.assertEqual(self.data, original)
        self.assertTrue(saved["checkpoint_persisted"])
        self.assertFalse(self.load(saved["record_id"])["checkpoint_persisted"])
        self.assertFalse(saved["historical_return_eligible"])
        self.assertFalse(saved["execution_authorized"])

    def test_whitespace_corruption_is_detected(self):
        saved = self.save()
        path = Path(saved["path"])
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            self.load(saved["record_id"])

    def test_corrupted_existing_record_is_never_overwritten(self):
        saved = self.save()
        path = Path(saved["path"])
        path.write_bytes(b"{}")
        with self.assertRaises(ValueError):
            self.save()
        self.assertEqual(path.read_bytes(), b"{}")
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_invalid_record_ids_are_rejected(self):
        for identifier in ("../other", "", "a" * 63, "G" * 64, None):
            with self.subTest(identifier=identifier):
                with self.assertRaises(ValueError):
                    self.load(identifier)

    def test_missing_record_is_reported(self):
        with self.assertRaises(FileNotFoundError):
            self.load("a" * 64)

    def test_nonfinite_values_are_rejected_before_publication(self):
        for value in (
            Decimal("NaN"), Decimal("Infinity"),
            float("nan"), float("inf"),
        ):
            with self.subTest(value=str(value)):
                self.data["valuations"]["bad"] = value
                with self.assertRaises(ValueError):
                    self.save()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_reserved_tag_collision_is_rejected(self):
        self.data["valuations"]["bad"] = {
            store.DECIMAL_TAG: "100",
        }
        with self.assertRaisesRegex(ValueError, "Reserved"):
            self.save()

    def test_unsupported_values_and_keys_are_rejected(self):
        for value in ({1: "value"}, {"items": {1, 2}}, {"items": (1, 2)}):
            with self.subTest(value=value):
                self.data["valuations"] = value
                with self.assertRaises(ValueError):
                    self.save()

    def test_authority_and_eligibility_claims_are_rejected(self):
        for name in (
            "checkpoint_persisted",
            "historical_return_eligible",
            "database_writes",
            "execution_authorized",
            "allocation_authority",
            "live_capital_authority",
        ):
            for value in (True, 0, None):
                with self.subTest(name=name, value=value):
                    self.data = checkpoint()
                    self.data[name] = value
                    with self.assertRaises(ValueError):
                        self.save()

    def test_timestamp_order_is_enforced(self):
        self.data["finished_at"] = "2026-09-27T08:59:00+00:00"
        with self.assertRaisesRegex(ValueError, "timestamps"):
            self.save()

    def test_audit_and_valuation_time_bindings_are_enforced(self):
        for name, field in (
            ("audit_snapshot", "sampled_at"),
            ("valuation_inputs", "snapshot_at"),
        ):
            with self.subTest(name=name):
                self.data = checkpoint()
                self.data[name][field] = "2026-09-27T08:59:00+00:00"
                with self.assertRaises(ValueError):
                    self.save()

    def test_installation_and_visibility_bindings_are_enforced(self):
        for field in ("audit_installation_id", "audit_visibility_snapshot"):
            with self.subTest(field=field):
                self.data = checkpoint()
                self.data["valuation_inputs"][field] = "different"
                with self.assertRaisesRegex(ValueError, "binding differs"):
                    self.save()

    def test_schema_and_methodology_are_checked(self):
        for field, value in (
            ("schema_version", True),
            ("schema_version", 2),
            ("methodology", "unknown"),
        ):
            with self.subTest(field=field):
                self.data = checkpoint()
                self.data[field] = value
                with self.assertRaises(ValueError):
                    self.save()

    def test_duplicate_json_fields_are_rejected(self):
        identifier = self.publish_raw(
            b'{"storage_schema_version":1,"storage_schema_version":1}'
        )
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.load(identifier)

    def test_bad_decimal_encoding_is_rejected(self):
        saved = self.save()
        envelope = json.loads(Path(saved["path"]).read_bytes())
        envelope["checkpoint"]["valuations"]["bad"] = {
            store.DECIMAL_TAG: "NaN"
        }
        raw = json.dumps(
            envelope, sort_keys=True, separators=(",", ":")
        ).encode()
        with self.assertRaises(ValueError):
            self.load(self.publish_raw(raw))

    def test_link_failure_leaves_no_published_or_temporary_file(self):
        with patch.object(store.os, "link", side_effect=OSError("link failed")):
            with self.assertRaises(OSError):
                self.save()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_write_sync_failure_cleans_temporary_file(self):
        with patch.object(store.os, "fsync", side_effect=OSError("sync failed")):
            with self.assertRaises(OSError):
                self.save()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_directory_sync_failure_is_reported_and_retry_recovers(self):
        real_fsync = store.os.fsync
        calls = 0

        def fail_second(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("directory sync failed")
            return real_fsync(descriptor)

        with patch.object(store.os, "fsync", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.save()

        # Publication may have occurred, but failure was not reported as success.
        self.assertEqual(len(list(self.directory.glob("*.json"))), 1)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])
        saved = self.save()
        self.assertEqual(self.load(saved["record_id"]), self.data)


if __name__ == "__main__":
    unittest.main()
