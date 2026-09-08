from datetime import timedelta
import runpy
import unittest
from unittest.mock import patch

from app.capital import validation_registry as registry
from app.capital import validation_collection as collection
from app.capital.collection_recovery import recover

context = runpy.run_path("tests/test_validation_collection.py")
Base = context["CollectionTests"]
NOW = context["NOW"]


class RecoveryTests(Base):
    def test_append_registry_failure_recovers(self):
        with patch.object(
            registry, "save_state", side_effect=OSError("registry interrupted")
        ):
            with self.assertRaises(OSError):
                self.capture()
        with patch.object(
            registry, "now_utc", return_value=NOW + timedelta(minutes=2)
        ):
            self.assertEqual(recover(self.plan_id), "recovered")
            self.assertEqual(recover(self.plan_id), "already_consistent")
        row = registry.get_plan(self.plan_id)
        self.assertEqual(row["witness_collection"]["store_checkpoint"]["count"], 1)

    def test_seal_registry_failure_recovers(self):
        self.capture()
        with (
            patch.object(
                registry, "now_utc", return_value=NOW + timedelta(days=3)
            ),
            patch.object(
                registry, "save_state", side_effect=OSError("registry interrupted")
            ),
        ):
            with self.assertRaises(OSError):
                collection.seal(self.plan_id)
        with patch.object(
            registry, "now_utc", return_value=NOW + timedelta(days=3)
        ):
            self.assertEqual(recover(self.plan_id), "recovered")
            row = registry.claim_plan(self.plan_id)
        self.assertEqual(row["witness_collection"]["status"], "sealed")

    def test_orphan_receipt_recovers(self):
        from app.capital.receipt_store import ReceiptStore
        with patch.object(
            ReceiptStore, "save_checkpoint", side_effect=OSError("checkpoint")
        ):
            with self.assertRaises(OSError):
                self.capture()
        with patch.object(
            registry, "now_utc", return_value=NOW + timedelta(minutes=2)
        ):
            self.assertEqual(recover(self.plan_id), "recovered")
        row = registry.get_plan(self.plan_id)
        self.assertEqual(row["witness_collection"]["store_checkpoint"]["count"], 1)

    def test_corrupt_pending_receipt_stays_blocked(self):
        from app.capital.receipt_store import ReceiptStore
        with patch.object(
            ReceiptStore, "save_checkpoint", side_effect=OSError("checkpoint")
        ):
            with self.assertRaises(OSError):
                self.capture()
        row = registry.get_plan(self.plan_id)
        store = collection.store_for(row["witness_collection"])
        path = store.directory / "receipt_00000001.json"
        path.write_text("{}")
        before = (registry.DIRECTORY / "registry.json").read_bytes()
        with self.assertRaises((ValueError, KeyError)):
            recover(self.plan_id)
        self.assertEqual(
            (registry.DIRECTORY / "registry.json").read_bytes(), before
        )


if __name__ == "__main__":
    suite = unittest.TestSuite(
        RecoveryTests(name) for name in (
            "test_append_registry_failure_recovers",
            "test_seal_registry_failure_recovers",
            "test_orphan_receipt_recovers",
            "test_corrupt_pending_receipt_stays_blocked",
        )
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
