"""Persistence and recovery tests for provenance receipt collections."""

from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_validation_quote_receipt import (
    ValidationQuoteReceiptTests as ReceiptFixture,
)
from app.capital.validation_quote_store import (
    ValidationQuoteReceiptStore,
)


class ValidationQuoteStoreTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ReceiptFixture("test_valid_receipt")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

        self.store = self.make_store()
        self.store.initialize()

    def make_store(self, bound_at=None):
        return ValidationQuoteReceiptStore(
            self.directory,
            envelope=self.fixture.envelope,
            expected_sha256=self.fixture.sha,
            bound_at=(
                bound_at or self.fixture.bound.isoformat()
            ),
        )

    def receipt(self, offset=0):
        recorded = (
            self.fixture.recorded
            + timedelta(seconds=offset)
        )
        return self.fixture.make(
            recorded_at=recorded.isoformat(),
        )

    def test_initialize_is_idempotent(self):
        before = self.store.read_checkpoint()
        after = self.store.initialize()
        self.assertEqual(before, after)

    def test_changed_binding_is_rejected(self):
        other = self.make_store(
            bound_at=(
                self.fixture.bound
                + timedelta(seconds=1)
            ).isoformat(),
        )
        with self.assertRaisesRegex(ValueError, "binding"):
            other.initialize()

    def test_append_seal_and_export(self):
        first = self.receipt()
        second = self.receipt(1)

        self.store.append(first)
        self.store.append(second)
        retained = deepcopy(self.store.seal())

        self.assertEqual(retained["count"], 2)
        self.assertTrue(retained["sealed"])
        self.assertEqual(
            self.store.export(retained),
            [first, second],
        )

    def test_equal_logging_time_is_rejected(self):
        self.store.append(self.receipt())

        with self.assertRaisesRegex(ValueError, "increase"):
            self.store.append(self.receipt())

        self.assertEqual(
            self.store.read_checkpoint()["count"],
            1,
        )

    def test_backward_logging_time_is_rejected(self):
        self.store.append(self.receipt(2))

        with self.assertRaisesRegex(ValueError, "increase"):
            self.store.append(self.receipt(1))

    def test_empty_collection_cannot_be_sealed(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            self.store.seal()

    def test_sealed_collection_rejects_append(self):
        self.store.append(self.receipt())
        self.store.seal()

        with self.assertRaisesRegex(ValueError, "sealed"):
            self.store.append(self.receipt(1))

    def test_export_requires_retained_checkpoint(self):
        self.store.append(self.receipt())
        retained = deepcopy(self.store.seal())
        retained["head"] = "0" * 64

        with self.assertRaises(ValueError):
            self.store.export(retained)

    def test_unsealed_export_is_rejected(self):
        checkpoint = self.store.append(self.receipt())

        with self.assertRaises(ValueError):
            self.store.export(checkpoint)

    def test_tampered_entry_is_rejected(self):
        self.store.append(self.receipt())
        path = self.directory / "receipt_00000001.json"
        entry = json.loads(path.read_text())
        entry["body"]["previous"] = "0" * 64
        path.write_text(json.dumps(entry))

        with self.assertRaises(ValueError):
            self.store.seal()

    def test_interrupted_append_supports_exact_retry(self):
        receipt = self.receipt()

        with patch.object(
            self.store,
            "save_checkpoint",
            side_effect=OSError("simulated checkpoint failure"),
        ):
            with self.assertRaises(OSError):
                self.store.append(receipt)

        self.assertEqual(
            self.store.read_checkpoint()["count"],
            0,
        )
        self.assertTrue(
            (self.directory / "receipt_00000001.json").exists()
        )

        recovered = self.store.append(receipt)
        self.assertEqual(recovered["count"], 1)

        retained = self.store.seal()
        self.assertEqual(
            self.store.export(retained),
            [receipt],
        )

    def test_interrupted_append_rejects_conflicting_retry(self):
        with patch.object(
            self.store,
            "save_checkpoint",
            side_effect=OSError("simulated checkpoint failure"),
        ):
            with self.assertRaises(OSError):
                self.store.append(self.receipt())

        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.store.append(self.receipt(1))

        self.assertEqual(
            self.store.read_checkpoint()["count"],
            0,
        )

    def test_sealing_rejects_uncommitted_tail(self):
        self.store.append(self.receipt())

        with patch.object(
            self.store,
            "save_checkpoint",
            side_effect=OSError("simulated checkpoint failure"),
        ):
            with self.assertRaises(OSError):
                self.store.append(self.receipt(1))

        with self.assertRaisesRegex(ValueError, "interrupted"):
            self.store.seal()


if __name__ == "__main__":
    unittest.main()
