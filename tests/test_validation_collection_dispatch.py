"""Routing tests for legacy and provider-time collection."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from app.capital import autonomy_validation_collection as worker


class ValidationCollectionDispatchTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(
            2026, 10, 6, 18, 0,
            tzinfo=timezone.utc,
        )

        self.row = {
            "plan_id": "validation_dispatch_test",
            "status": "registered",
            "envelope": {
                "plan": {
                    "schema_version": 2,
                    "created_by": worker.PREFIX + "isolated-test",
                    "start": (
                        self.now + timedelta(hours=1)
                    ).isoformat(),
                    "end_exclusive": (
                        self.now + timedelta(hours=2)
                    ).isoformat(),
                },
            },
        }

        self.authorization = patch.object(
            worker,
            "_authorize",
        )
        self.authorization.start()
        self.addCleanup(self.authorization.stop)

        self.lookup = patch.object(
            worker.registry,
            "get_plan",
            return_value=self.row,
        )
        self.lookup.start()
        self.addCleanup(self.lookup.stop)

        self.clock = patch.object(
            worker.registry,
            "now_utc",
            return_value=self.now,
        )
        self.clock.start()
        self.addCleanup(self.clock.stop)

        self.capture_patch = patch(
            "app.capital.validation_provider_capture."
            "capture_provider_cycle",
            return_value={"status": "captured"},
        )
        self.capture = self.capture_patch.start()
        self.addCleanup(self.capture_patch.stop)

    def test_version_two_uses_provider_collector(self):
        result = worker._advance(self.row["plan_id"])

        self.capture.assert_called_once_with(
            self.row["plan_id"]
        )
        self.assertEqual(result["status"], "captured")

    def test_unowned_version_two_plan_is_skipped(self):
        self.row["envelope"]["plan"]["created_by"] = (
            "manual-unrelated-plan"
        )

        result = worker._advance(self.row["plan_id"])

        self.assertEqual(result["status"], "skipped")
        self.capture.assert_not_called()

    def test_claimed_version_two_plan_is_skipped(self):
        self.row["status"] = "running"

        result = worker._advance(self.row["plan_id"])

        self.assertEqual(result["status"], "skipped")
        self.capture.assert_not_called()

    def test_completed_version_two_plan_is_skipped(self):
        self.row["status"] = "completed"

        result = worker._advance(self.row["plan_id"])

        self.assertEqual(result["status"], "skipped")
        self.capture.assert_not_called()

    def test_legacy_plan_keeps_legacy_route(self):
        self.row["envelope"]["plan"]["schema_version"] = 1

        result = worker._advance(self.row["plan_id"])

        self.assertEqual(
            result["status"],
            "waiting_for_collection_window",
        )
        self.capture.assert_not_called()

    def test_provider_failure_does_not_fall_back(self):
        self.capture.side_effect = ValueError(
            "isolated provider collection mismatch"
        )

        with self.assertRaisesRegex(
            ValueError,
            "mismatch",
        ):
            worker._advance(self.row["plan_id"])

        self.capture.assert_called_once()

    def test_dispatch_does_not_change_plan(self):
        before = deepcopy(self.row)

        worker._advance(self.row["plan_id"])

        self.assertEqual(self.row, before)


if __name__ == "__main__":
    unittest.main()
