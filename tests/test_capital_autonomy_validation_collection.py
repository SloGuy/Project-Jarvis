"""Collection timing and failure tests using an isolated registry."""

from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_capital_autonomy_validation_registration as fixtures

from app.capital import autonomy_validation_collection as worker
from app.capital.autonomy_policy import CapitalOperatingPolicy
from app.capital.observation_witness import digest


NOW = fixtures.NOW


class CapitalValidationCollectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ValidationRegistrationRetryTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.row = self.fixture.register()
        self.plan_id = self.row["plan_id"]

        self.mock(worker.registry, "get_plan", return_value=self.row)
        self.policy = self.mock(
            worker,
            "read_operating_policy",
            return_value=CapitalOperatingPolicy(),
        )
        self.binding = self.mock(worker, "current_binding")
        self.bind = self.mock(worker.collection, "bind")
        self.capture = self.mock(
            worker.collection, "capture_next", return_value=1
        )
        self.seal = self.mock(worker.collection, "seal")
        self.validate = self.mock(worker.collection, "validate_collection")

        self.checkpoint = {"count": 0, "head": None, "sealed": False}
        self.store = SimpleNamespace(
            directory=Path(worker.registry.DIRECTORY) / "receipt-fixtures",
            read_checkpoint=lambda: self.checkpoint,
        )
        self.store.directory.mkdir()
        self.mock(worker.collection, "store_for", return_value=self.store)

        self.row["envelope"]["plan"]["start"] = (
            NOW + timedelta(minutes=20)
        ).isoformat()

    def mock(self, target, name, **kwargs):
        mocked = patch.object(target, name, **kwargs)
        result = mocked.start()
        self.addCleanup(mocked.stop)
        return result

    def witness(self):
        self.row["witness_collection"] = {
            "status": "collecting",
            "bound_at": (NOW - timedelta(minutes=10)).isoformat(),
            "store_checkpoint": self.checkpoint,
        }

    def advance(self):
        return worker._advance(self.plan_id)

    def test_distant_window_does_not_bind(self):
        self.row["envelope"]["plan"]["start"] = (
            NOW + timedelta(hours=2)
        ).isoformat()
        self.assertEqual(
            self.advance()["status"], "waiting_for_collection_window"
        )
        self.bind.assert_not_called()
        self.capture.assert_not_called()

    def test_unbound_plan_is_bound_before_start(self):
        self.assertEqual(self.advance()["status"], "bound")
        self.bind.assert_called_once_with(self.plan_id)
        self.capture.assert_not_called()

    def test_missed_binding_deadline_is_rejected(self):
        self.fixture.clock.return_value = NOW + timedelta(minutes=20)
        with self.assertRaises(ValueError):
            self.advance()
        self.bind.assert_not_called()

    def test_open_collection_captures(self):
        self.witness()
        result = self.advance()
        self.assertEqual(result["status"], "captured")
        self.assertEqual(result["receipt_count"], 1)
        self.capture.assert_called_once_with(self.plan_id)

    def test_recent_receipt_waits_for_capture_interval(self):
        self.witness()
        payload = {
            "witnessed_at": (NOW - timedelta(seconds=30)).isoformat()
        }
        body = {"receipt": {"fixture": True}}
        entry = {"body": body, "sha256": digest(body)}
        self.checkpoint.update({"count": 1, "head": entry["sha256"]})
        path = self.store.directory / "receipt_00000001.json"
        path.write_text(json.dumps(entry))
        self.mock(worker, "verify_receipt", return_value=payload)

        self.assertEqual(self.advance()["status"], "waiting_for_capture")
        self.capture.assert_not_called()

    def test_deadline_seals_instead_of_capturing(self):
        self.witness()
        self.checkpoint["count"] = 1
        self.fixture.clock.return_value = NOW + timedelta(days=7)
        self.assertEqual(self.advance()["status"], "sealed")
        self.seal.assert_called_once_with(self.plan_id)
        self.capture.assert_not_called()

    def test_checkpoint_mismatch_blocks_capture(self):
        self.witness()
        self.store.read_checkpoint = lambda: {"different": True}
        with self.assertRaises(ValueError):
            self.advance()
        self.capture.assert_not_called()
        self.seal.assert_not_called()

    def test_paused_automation_continues_existing_collection(self):
        self.witness()
        self.policy.return_value = CapitalOperatingPolicy(paused=True)
        self.assertEqual(self.advance()["status"], "captured")

    def test_disabled_cycle_performs_no_collection(self):
        self.policy.return_value = CapitalOperatingPolicy(enabled=False)
        self.assertEqual(worker.run_collection_cycle()["status"], "disabled")
        self.bind.assert_not_called()
        self.capture.assert_not_called()

    def test_source_failure_is_reported_without_capture(self):
        self.witness()
        self.validate.side_effect = ValueError("Witness source changed.")
        result = worker.run_collection_cycle()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["outcomes"][0]["status"], "blocked")
        self.capture.assert_not_called()
        self.seal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
