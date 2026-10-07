"""Isolated registry tests for provider-time collection binding."""

from contextlib import ExitStack
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_validation_quote_receipt as receipt_fixture

from app.capital import validation_registry as registry
from app.capital.validation_input_contract import (
    make_input_contract,
)
from app.capital.validation_plan import plan_digest
from app.capital.validation_provider_collection import (
    bind_provider_collection,
    materialize_provider_collection,
    refresh_collection,
    seal_provider_collection,
    store_for,
    validate_provider_collection,
)


class ValidationProviderCollectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = (
            receipt_fixture.ValidationQuoteReceiptTests(
                "test_valid_receipt"
            )
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

        self.patches = ExitStack()
        self.addCleanup(self.patches.close)

        self.patches.enter_context(patch.object(
            registry,
            "DIRECTORY",
            Path(self.temporary.name) / "registry",
        ))

        self.now = self.patches.enter_context(patch.object(
            registry,
            "now_utc",
            return_value=self.fixture.bound,
        ))

        self.authorize = self.patches.enter_context(patch(
            "app.capital.validation_provider_collection."
            "authorize_collection"
        ))

        self.binding = self.patches.enter_context(patch(
            "app.capital.validation_provider_collection.check_current_binding"
        ))

        self.plan = deepcopy(
            self.fixture.envelope["plan"]
        )
        self.plan["schema_version"] = 2
        self.plan["policy"] = {
            "max_price_age_seconds": 120,
        }
        self.plan["input_contract"] = make_input_contract(
            policy=self.plan["policy"],
        )

        self.sha = plan_digest(self.plan)
        self.envelope = {
            "plan": self.plan,
            "sha256": self.sha,
        }
        self.plan_id = "validation_isolated_test"

        with registry.locked_state(write=True) as state:
            state["plans"][self.plan_id] = {
                "plan_id": self.plan_id,
                "envelope": deepcopy(self.envelope),
                "registered_sha256": self.sha,
                "status": "registered",
                "history": [{
                    "status": "registered",
                    "at": self.plan["created_at"],
                }],
            }

    def row(self):
        return registry.get_plan(self.plan_id)

    def receipt(self):
        return self.fixture.make(
            envelope=self.envelope,
            expected_sha256=self.sha,
        )

    def append_receipt(self):
        row = self.row()
        collection = row["provider_collection"]
        store = store_for(row, collection)
        checkpoint = store.append(self.receipt())

        with registry.locked_state(write=True) as state:
            collection = state["plans"][
                self.plan_id
            ]["provider_collection"]
            collection["store_checkpoint"] = checkpoint
            refresh_collection(collection)

    def test_binding_records_checkpoint_and_history(self):
        collection = bind_provider_collection(self.plan_id)
        row = self.row()

        self.assertEqual(collection["status"], "collecting")
        self.assertEqual(
            collection["store_checkpoint"]["count"],
            0,
        )
        self.assertEqual(
            row["history"][-1]["status"],
            "provider_collection_bound",
        )
        self.assertNotIn("witness_collection", row)
        self.binding.assert_called_once()

    def test_binding_retry_is_idempotent(self):
        first = bind_provider_collection(self.plan_id)
        history_length = len(self.row()["history"])

        second = bind_provider_collection(self.plan_id)

        self.assertEqual(first, second)
        self.assertEqual(
            len(self.row()["history"]),
            history_length,
        )

    def test_binding_at_start_is_rejected(self):
        self.now.return_value = self.fixture.start

        with self.assertRaisesRegex(ValueError, "before"):
            bind_provider_collection(self.plan_id)

        self.assertNotIn(
            "provider_collection",
            self.row(),
        )

    def test_legacy_plan_is_rejected(self):
        legacy = deepcopy(self.fixture.envelope)

        with registry.locked_state(write=True) as state:
            row = state["plans"][self.plan_id]
            row["envelope"] = legacy
            row["registered_sha256"] = legacy["sha256"]

        with self.assertRaisesRegex(ValueError, "version-2"):
            bind_provider_collection(self.plan_id)

    def test_mixed_collection_formats_are_rejected(self):
        with registry.locked_state(write=True) as state:
            state["plans"][self.plan_id][
                "witness_collection"
            ] = {}

        with self.assertRaisesRegex(ValueError, "mixed"):
            bind_provider_collection(self.plan_id)

    def test_denied_authority_prevents_binding(self):
        self.authorize.side_effect = PermissionError(
            "isolated authorization denial"
        )

        with self.assertRaises(PermissionError):
            bind_provider_collection(self.plan_id)

        self.assertNotIn(
            "provider_collection",
            self.row(),
        )

    def test_binding_failure_prevents_registry_attachment(self):
        self.binding.side_effect = ValueError(
            "isolated binding mismatch"
        )

        with self.assertRaises(ValueError):
            bind_provider_collection(self.plan_id)

        self.assertNotIn(
            "provider_collection",
            self.row(),
        )

    def test_changed_sources_prevent_binding(self):
        with patch(
            "app.capital.validation_input_contract."
            "capture_input_sources",
            return_value={},
        ):
            with self.assertRaisesRegex(
                ValueError,
                "sources changed",
            ):
                bind_provider_collection(self.plan_id)

    def test_early_sealing_is_rejected(self):
        bind_provider_collection(self.plan_id)
        self.append_receipt()
        self.now.return_value = self.fixture.end - timedelta(
            seconds=1
        )

        with self.assertRaisesRegex(ValueError, "not ended"):
            seal_provider_collection(self.plan_id)

    def test_sealed_collection_materializes(self):
        bind_provider_collection(self.plan_id)
        self.append_receipt()
        self.now.return_value = self.fixture.end

        collection = seal_provider_collection(self.plan_id)
        packet = materialize_provider_collection(self.row())

        self.assertEqual(collection["status"], "sealed")
        self.assertEqual(
            packet["receipts"],
            [self.receipt()],
        )
        self.assertEqual(
            self.row()["status"],
            "registered",
        )

    def test_unsealed_collection_cannot_materialize(self):
        bind_provider_collection(self.plan_id)

        with self.assertRaisesRegex(ValueError, "not sealed"):
            materialize_provider_collection(self.row())

    def test_unretained_checkpoint_requires_recovery(self):
        bind_provider_collection(self.plan_id)
        row = self.row()

        store_for(
            row,
            row["provider_collection"],
        ).append(self.receipt())

        with self.assertRaisesRegex(ValueError, "recovery"):
            validate_provider_collection(self.row())

    def test_changed_collection_hash_is_rejected(self):
        bind_provider_collection(self.plan_id)

        with registry.locked_state(write=True) as state:
            state["plans"][self.plan_id][
                "provider_collection"
            ]["sha256"] = "0" * 64

        with self.assertRaisesRegex(ValueError, "integrity"):
            validate_provider_collection(self.row())


if __name__ == "__main__":
    unittest.main()
