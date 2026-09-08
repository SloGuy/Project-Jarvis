from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from app.capital import validation_registry as registry

fixture = runpy.run_path("tests/test_validation_plan.py")
draft = fixture["draft"]
NOW = fixture["NOW"]
AFTER = datetime(2030, 1, 3, tzinfo=timezone.utc)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        patcher = patch.object(registry, "DIRECTORY", self.directory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def register(self, value=None):
        with patch.object(registry, "now_utc", return_value=NOW):
            return registry.register_plan(value if value is not None else draft())

    def test_roundtrip_and_overlap(self):
        row = self.register()
        self.assertEqual(registry.get_plan(row["plan_id"]), row)
        before = (self.directory / "registry.json").read_bytes()
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual((self.directory / "registry.json").read_bytes(), before)

    def test_adjacent_period_allowed(self):
        self.register()
        value = draft()
        value["start"] = value["end_exclusive"]
        value["end_exclusive"] = "2030-01-03T02:00:00+00:00"
        self.register(value)

    def test_claim_once_and_complete(self):
        row = self.register()
        with patch.object(registry, "now_utc", return_value=NOW):
            with self.assertRaises(ValueError):
                registry.claim_plan(row["plan_id"])
        with patch.object(registry, "now_utc", return_value=AFTER):
            running = registry.claim_plan(row["plan_id"])
            with self.assertRaises(ValueError):
                registry.claim_plan(row["plan_id"])
            with self.assertRaises(ValueError):
                registry.finish_plan(
                    row["plan_id"], "wrong", succeeded=True, detail="result"
                )
            result = registry.finish_plan(
                row["plan_id"], running["run_token"],
                succeeded=True, detail="synthetic result reference",
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["history"]), 3)

    def test_failed_run_remains_reserved(self):
        row = self.register()
        with patch.object(registry, "now_utc", return_value=AFTER):
            running = registry.claim_plan(row["plan_id"])
            registry.finish_plan(
                row["plan_id"], running["run_token"],
                succeeded=False, detail="Synthetic failure",
            )
            with self.assertRaises(ValueError):
                registry.claim_plan(row["plan_id"])
        with self.assertRaises(ValueError):
            self.register()

    def test_damaged_registry_preserved(self):
        path = self.directory / "registry.json"
        path.write_text("{broken")
        before = path.read_bytes()
        with self.assertRaises(RuntimeError):
            self.register()
        self.assertEqual(path.read_bytes(), before)

    def test_plan_tamper_blocks_reads(self):
        row = self.register()
        import json
        path = self.directory / "registry.json"
        state = json.loads(path.read_text())
        state["plans"][row["plan_id"]]["envelope"]["plan"]["fee_bps"] = "0"
        path.write_text(json.dumps(state))
        before = path.read_bytes()
        with self.assertRaises(RuntimeError):
            registry.get_plan(row["plan_id"])
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
