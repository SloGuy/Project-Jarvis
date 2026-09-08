import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.capital.receipt_store import ReceiptStore
from app.capital.observation_witness import make_receipt


def receipt(ident=1):
    return make_receipt([{
        "id": ident, "asset_id": 2, "provider": "CoinGecko",
        "price_usd": "100",
        "observed_at": "2026-09-08T12:00:00+00:00",
    }], "2026-09-08T12:01:00+00:00")


class StoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.store = ReceiptStore(self.directory)
        self.store.initialize({"plan_sha256": "synthetic"})

    def test_roundtrip_and_sealed_append_rejection(self):
        self.store.append(receipt())
        checkpoint = self.store.seal()
        self.assertEqual(self.store.export(checkpoint), [receipt()])
        with self.assertRaises(ValueError):
            self.store.append(receipt(2))

    def test_changed_receipt_rejected(self):
        self.store.append(receipt())
        path = self.directory / "receipt_00000001.json"
        value = json.loads(path.read_text())
        value["body"]["receipt"]["payload"]["observations"][0]["price_usd"] = "999"
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            self.store.seal()

    def test_missing_receipt_rejected(self):
        self.store.append(receipt())
        checkpoint = self.store.seal()
        (self.directory / "receipt_00000001.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self.store.export(checkpoint)

    def test_interrupted_append_can_retry_exact_receipt(self):
        with patch.object(
            self.store, "save_checkpoint", side_effect=OSError("interrupted")
        ):
            with self.assertRaises(OSError):
                self.store.append(receipt())
        self.assertEqual(self.store.read_checkpoint()["count"], 0)
        with self.assertRaises(ValueError):
            self.store.append(receipt(2))
        self.store.append(receipt())
        self.assertEqual(self.store.seal()["count"], 1)

    def test_old_checkpoint_cannot_hide_later_receipts(self):
        self.store.append(receipt())
        old = self.store.read_checkpoint()
        self.store.append(receipt(2))
        self.store.seal()
        with self.assertRaises(ValueError):
            self.store.export(old)

    def test_checkpoint_stays_small(self):
        for index in range(100):
            self.store.append(receipt(index + 1))
        self.assertLess(
            (self.directory / "checkpoint.json").stat().st_size, 1024
        )
        self.assertEqual(len(self.store.export(self.store.seal())), 100)


if __name__ == "__main__":
    unittest.main()
