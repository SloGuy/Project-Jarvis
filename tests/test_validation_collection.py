import json
from datetime import timedelta
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from app.capital import validation_registry as registry
from app.capital import validation_collection as collection
from app.capital.observation_witness import make_receipt

fixture = runpy.run_path("tests/test_validation_plan.py")
NOW = fixture["NOW"]


class CollectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        handle = patch.object(registry, "DIRECTORY", self.directory / "registry")
        handle.start()
        self.addCleanup(handle.stop)
        with patch.object(registry, "now_utc", return_value=NOW):
            self.row = registry._register_plan(
                fixture["draft"](), validate_binding=lambda plan: None
            )
            collection.bind(self.row["plan_id"])
        self.plan_id = self.row["plan_id"]

    def capture(self):
        when = NOW + timedelta(minutes=1)
        receipt = make_receipt([{
            "id": 1, "asset_id": 1, "provider": "Finnhub",
            "price_usd": "100",
            "observed_at": NOW.isoformat(),
        }], when)
        path = self.directory / "receipt.json"
        path.write_text(json.dumps(receipt))
        with (
            patch.object(collection, "capture", return_value=path),
            patch.object(registry, "now_utc", return_value=when),
        ):
            return collection.capture_next(self.plan_id)

    def test_source_change_blocks_capture_without_writes(self):
        path = registry.DIRECTORY / "registry.json"
        before = path.read_bytes()
        with (
            patch.object(collection, "source_manifest", return_value={}),
            patch.object(collection, "capture") as capture,
        ):
            with self.assertRaisesRegex(ValueError, "source changed"):
                collection.capture_next(self.plan_id)
            capture.assert_not_called()
        self.assertEqual(path.read_bytes(), before)

    def test_registry_keeps_only_compact_reference(self):
        self.capture()
        row = registry.get_plan(self.plan_id)
        value = row["witness_collection"]
        self.assertNotIn("receipts", value)
        self.assertEqual(value["store_checkpoint"]["count"], 1)
        with patch.object(
            registry, "now_utc", return_value=NOW + timedelta(days=3)
        ):
            collection.seal(self.plan_id)
        sealed = registry.get_plan(self.plan_id)["witness_collection"]
        packet = collection.materialize_collection(sealed)
        self.assertEqual(len(packet["receipts"]), 1)
        self.assertNotIn(
            "receipts",
            registry.get_plan(self.plan_id)["witness_collection"],
        )

    def test_capture_seal_and_claim(self):
        self.assertEqual(self.capture(), 1)
        with patch.object(
            registry, "now_utc", return_value=NOW + timedelta(days=3)
        ):
            retained = collection.seal(self.plan_id)
            self.assertEqual(collection.seal(self.plan_id), retained)
            running = registry.claim_plan(self.plan_id)
        self.assertEqual(running["witness_collection"]["sha256"], retained)
        self.assertEqual(running["status"], "running")

    def test_unsealed_collection_cannot_be_claimed(self):
        with patch.object(
            registry, "now_utc", return_value=NOW + timedelta(days=3)
        ):
            with self.assertRaisesRegex(ValueError, "sealed"):
                registry.claim_plan(self.plan_id)

    def test_early_and_empty_sealing_rejected(self):
        with patch.object(registry, "now_utc", return_value=NOW):
            with self.assertRaises(ValueError):
                collection.seal(self.plan_id)
        with patch.object(
            registry, "now_utc", return_value=NOW + timedelta(days=3)
        ):
            with self.assertRaisesRegex(ValueError, "empty"):
                collection.seal(self.plan_id)

    def test_capture_failure_preserves_registry(self):
        path = registry.DIRECTORY / "registry.json"
        before = path.read_bytes()
        with (
            patch.object(collection, "capture", side_effect=RuntimeError("capture")),
            patch.object(registry, "now_utc", return_value=NOW),
        ):
            with self.assertRaises(RuntimeError):
                collection.capture_next(self.plan_id)
        self.assertEqual(path.read_bytes(), before)

    def test_changed_collection_rejected(self):
        self.capture()
        path = registry.DIRECTORY / "registry.json"
        state = json.loads(path.read_text())
        state["plans"][self.plan_id]["witness_collection"]["store_checkpoint"]["count"] = 0
        path.write_text(json.dumps(state))
        with patch.object(
            registry, "now_utc", return_value=NOW + timedelta(days=3)
        ):
            with self.assertRaisesRegex(ValueError, "integrity"):
                registry.claim_plan(self.plan_id)


if __name__ == "__main__":
    unittest.main()
