"""Append-only receipt files with a compact atomic checkpoint."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile

from app.capital.observation_witness import digest, verify_receipt


def write_atomic(path, value):
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".pending_")
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, sort_keys=True, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class ReceiptStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    @contextmanager
    def lock(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / "store.lock").open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def read_checkpoint(self):
        value = json.loads(
            (self.directory / "checkpoint.json").read_text()
        )
        if set(value) != {
            "schema_version", "binding", "count", "head", "sealed", "sha256"
        }:
            raise ValueError("Invalid receipt checkpoint.")
        body = {k: v for k, v in value.items() if k != "sha256"}
        if (
            value["schema_version"] != 1
            or type(value["count"]) is not int
            or value["count"] < 0
            or type(value["sealed"]) is not bool
            or value["sha256"] != digest(body)
        ):
            raise ValueError("Receipt checkpoint integrity mismatch.")
        return value

    def save_checkpoint(self, value):
        value["sha256"] = digest({
            k: v for k, v in value.items() if k != "sha256"
        })
        write_atomic(self.directory / "checkpoint.json", value)

    def initialize(self, binding):
        if not isinstance(binding, dict) or not binding:
            raise ValueError("Collection binding is required.")
        with self.lock():
            path = self.directory / "checkpoint.json"
            if path.exists():
                existing = self.read_checkpoint()
                if existing["binding"] != binding:
                    raise ValueError("Collection binding changed.")
                return existing
            if any(self.directory.glob("receipt_*.json")):
                raise ValueError("Receipts exist without a checkpoint.")
            checkpoint = {
                "schema_version": 1,
                "binding": binding,
                "count": 0,
                "head": None,
                "sealed": False,
            }
            self.save_checkpoint(checkpoint)
            return checkpoint

    def append(self, receipt):
        verify_receipt(receipt)
        with self.lock():
            checkpoint = self.read_checkpoint()
            if checkpoint["sealed"]:
                raise ValueError("Receipt store is sealed.")
            index = checkpoint["count"] + 1
            body = {
                "index": index,
                "previous": checkpoint["head"],
                "receipt": receipt,
            }
            entry = {"body": body, "sha256": digest(body)}
            path = self.directory / f"receipt_{index:08d}.json"
            if path.exists():
                # Interrupted append may leave this exact entry behind.
                if json.loads(path.read_text()) != entry:
                    raise ValueError("Uncommitted receipt conflicts with retry.")
            else:
                write_atomic(path, entry)
            checkpoint["count"] = index
            checkpoint["head"] = entry["sha256"]
            self.save_checkpoint(checkpoint)
            return checkpoint

    def verify_entries(self, checkpoint):
        previous = None
        receipts = []
        for index in range(1, checkpoint["count"] + 1):
            path = self.directory / f"receipt_{index:08d}.json"
            entry = json.loads(path.read_text())
            if set(entry) != {"body", "sha256"}:
                raise ValueError("Invalid receipt entry.")
            body = entry["body"]
            if (
                set(body) != {"index", "previous", "receipt"}
                or body["index"] != index
                or body["previous"] != previous
                or digest(body) != entry["sha256"]
            ):
                raise ValueError("Receipt chain integrity mismatch.")
            verify_receipt(body["receipt"])
            receipts.append(body["receipt"])
            previous = entry["sha256"]
        if previous != checkpoint["head"]:
            raise ValueError("Receipt chain head mismatch.")
        return receipts

    def seal(self):
        with self.lock():
            checkpoint = self.read_checkpoint()
            if checkpoint["count"] == 0:
                raise ValueError("Cannot seal an empty receipt store.")
            self.verify_entries(checkpoint)
            extra = self.directory / (
                f"receipt_{checkpoint['count'] + 1:08d}.json"
            )
            if extra.exists():
                raise ValueError("Recover interrupted append before sealing.")
            checkpoint["sealed"] = True
            self.save_checkpoint(checkpoint)
            return checkpoint

    def export(self, expected_checkpoint):
        with self.lock():
            checkpoint = self.read_checkpoint()
            if checkpoint != expected_checkpoint or not checkpoint["sealed"]:
                raise ValueError("Sealed checkpoint differs from retained reference.")
            return self.verify_entries(checkpoint)
