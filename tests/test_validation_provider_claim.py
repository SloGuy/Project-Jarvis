"""Single-use claims require sealed provider-time evidence."""

from copy import deepcopy
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

import test_validation_provider_collection as collection_fixture

from app.capital import validation_registry as registry
from app.capital.validation_provider_collection import (
    bind_provider_collection,
    seal_provider_collection,
    store_for,
)


class ValidationProviderClaimTests(unittest.TestCase):
    def setUp(self):
        self.fixture = (
            collection_fixture.ValidationProviderCollectionTests(
                "test_binding_records_checkpoint_and_history"
            )
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

        self.plan_id = self.fixture.plan_id
        self.base = self.fixture.fixture

    def prepare_sealed(self):
        bind_provider_collection(self.plan_id)
        self.fixture.append_receipt()
        self.fixture.now.return_value = self.base.end
        seal_provider_collection(self.plan_id)

    def assert_unclaimed(self):
        row = registry.get_plan(self.plan_id)
        self.assertEqual(row["status"], "registered")
        self.assertNotIn("run_token", row)

    def test_valid_sealed_collection_allows_claim(self):
        self.prepare_sealed()

        row = registry.claim_plan(self.plan_id)

        self.assertEqual(row["status"], "running")
        self.assertTrue(row["run_token"])
        self.assertEqual(
            row["history"][-1]["status"],
            "running",
        )

    def test_claim_is_single_use(self):
        self.prepare_sealed()
        registry.claim_plan(self.plan_id)

        with self.assertRaisesRegex(ValueError, "claimed"):
            registry.claim_plan(self.plan_id)

    def test_before_period_end_is_rejected(self):
        bind_provider_collection(self.plan_id)
        self.fixture.now.return_value = (
            self.base.end - timedelta(seconds=1)
        )

        with self.assertRaisesRegex(ValueError, "not ended"):
            registry.claim_plan(self.plan_id)

        self.assert_unclaimed()

    def test_missing_provider_collection_is_rejected(self):
        self.fixture.now.return_value = self.base.end

        with self.assertRaises(ValueError):
            registry.claim_plan(self.plan_id)

        self.assert_unclaimed()

    def test_unsealed_collection_is_rejected(self):
        bind_provider_collection(self.plan_id)
        self.fixture.append_receipt()
        self.fixture.now.return_value = self.base.end

        with self.assertRaisesRegex(ValueError, "not sealed"):
            registry.claim_plan(self.plan_id)

        self.assert_unclaimed()

    def test_changed_sources_do_not_consume_claim(self):
        self.prepare_sealed()

        with patch(
            "app.capital.validation_input_contract."
            "capture_input_sources",
            return_value={},
        ):
            with self.assertRaisesRegex(
                ValueError,
                "sources changed",
            ):
                registry.claim_plan(self.plan_id)

        self.assert_unclaimed()

    def test_checkpoint_drift_does_not_consume_claim(self):
        self.prepare_sealed()
        row = registry.get_plan(self.plan_id)
        store = store_for(
            row,
            row["provider_collection"],
        )
        checkpoint = store.read_checkpoint()
        checkpoint["head"] = "0" * 64
        store.save_checkpoint(checkpoint)

        with self.assertRaises(ValueError):
            registry.claim_plan(self.plan_id)

        self.assert_unclaimed()

    def test_receipt_corruption_does_not_consume_claim(self):
        self.prepare_sealed()
        row = registry.get_plan(self.plan_id)
        store = store_for(
            row,
            row["provider_collection"],
        )

        path = store.directory / "receipt_00000001.json"
        entry = json.loads(path.read_text())
        entry["sha256"] = "0" * 64
        path.write_text(json.dumps(entry))

        with self.assertRaises(ValueError):
            registry.claim_plan(self.plan_id)

        self.assert_unclaimed()

    def test_mixed_formats_do_not_consume_claim(self):
        self.prepare_sealed()

        with registry.locked_state(write=True) as state:
            state["plans"][self.plan_id][
                "witness_collection"
            ] = {}

        with self.assertRaisesRegex(ValueError, "mixed"):
            registry.claim_plan(self.plan_id)

        self.assert_unclaimed()

    def test_legacy_plan_without_witness_keeps_original_claim(self):
        legacy = deepcopy(self.base.envelope)

        with registry.locked_state(write=True) as state:
            row = state["plans"][self.plan_id]
            row["envelope"] = legacy
            row["registered_sha256"] = legacy["sha256"]

        self.fixture.now.return_value = self.base.end
        row = registry.claim_plan(self.plan_id)

        self.assertEqual(row["status"], "running")

    def test_legacy_plan_rejects_provider_collection(self):
        legacy = deepcopy(self.base.envelope)

        with registry.locked_state(write=True) as state:
            row = state["plans"][self.plan_id]
            row["envelope"] = legacy
            row["registered_sha256"] = legacy["sha256"]
            row["provider_collection"] = {}

        self.fixture.now.return_value = self.base.end

        with self.assertRaisesRegex(ValueError, "Legacy"):
            registry.claim_plan(self.plan_id)

        self.assert_unclaimed()


if __name__ == "__main__":
    unittest.main()
