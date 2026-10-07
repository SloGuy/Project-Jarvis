"""Separate append-only storage for validation provenance receipts.

Uses the existing atomic checkpoint mechanics without changing
legacy receipt validation. Binding and source hashes must be retained.
"""

from copy import deepcopy
import json

from app.capital.observation_witness import digest
from app.capital.receipt_store import ReceiptStore, write_atomic
from app.capital.validation_plan import timestamp, verify_plan
from app.capital.validation_quote_receipt import (
    verify_validation_quote_receipt,
)


MAX_RECEIPTS = 10000


class ValidationQuoteReceiptStore(ReceiptStore):
    def __init__(
        self,
        directory,
        *,
        envelope,
        expected_sha256,
        bound_at,
    ):
        super().__init__(directory)

        self.envelope = deepcopy(envelope)
        self.expected_sha256 = expected_sha256
        self.bound_at = timestamp(bound_at).isoformat()

        plan = verify_plan(
            self.envelope,
            expected_sha256=self.expected_sha256,
        )

        bound = timestamp(self.bound_at)

        if not (
            timestamp(plan["created_at"])
            <= bound
            < timestamp(plan["start"])
        ):
            raise ValueError("Invalid prospective collection binding.")

        self.binding = {
            "receipt_type": "provider_time_v1",
            "plan_sha256": self.expected_sha256,
            "bound_at": self.bound_at,
        }

    def initialize(self):
        return super().initialize(deepcopy(self.binding))

    def read_checkpoint(self):
        checkpoint = super().read_checkpoint()

        if checkpoint["binding"] != self.binding:
            raise ValueError("Provenance collection binding changed.")

        if checkpoint["count"] > MAX_RECEIPTS:
            raise ValueError("Provenance receipt limit exceeded.")

        if checkpoint["count"] == 0:
            if checkpoint["head"] is not None:
                raise ValueError("Empty collection has a chain head.")
        elif (
            not isinstance(checkpoint["head"], str)
            or len(checkpoint["head"]) != 64
        ):
            raise ValueError("Invalid provenance chain head.")

        return checkpoint

    def _verify_receipt(self, receipt):
        return verify_validation_quote_receipt(
            receipt=receipt,
            envelope=self.envelope,
            expected_sha256=self.expected_sha256,
            expected_bound_at=self.bound_at,
        )

    def _read_entry(self, index):
        path = self.directory / f"receipt_{index:08d}.json"
        entry = json.loads(path.read_text())

        if (
            not isinstance(entry, dict)
            or set(entry) != {"body", "sha256"}
        ):
            raise ValueError("Invalid provenance chain entry.")

        body = entry["body"]

        if (
            not isinstance(body, dict)
            or set(body) != {"index", "previous", "receipt"}
            or type(body["index"]) is not int
            or body["index"] != index
            or digest(body) != entry["sha256"]
        ):
            raise ValueError("Provenance chain integrity mismatch.")

        payload = self._verify_receipt(body["receipt"])

        return entry, payload

    def append(self, receipt):
        # Freeze the supplied receipt before validation and persistence.
        receipt = deepcopy(receipt)
        payload = self._verify_receipt(receipt)

        with self.lock():
            checkpoint = self.read_checkpoint()

            if checkpoint["sealed"]:
                raise ValueError("Provenance collection is sealed.")

            if checkpoint["count"] >= MAX_RECEIPTS:
                raise ValueError("Provenance receipt limit reached.")

            if checkpoint["count"]:
                previous_entry, previous_payload = self._read_entry(
                    checkpoint["count"]
                )

                if previous_entry["sha256"] != checkpoint["head"]:
                    raise ValueError("Provenance chain head mismatch.")

                if (
                    timestamp(payload["recorded_at"])
                    <= timestamp(previous_payload["recorded_at"])
                ):
                    raise ValueError("Receipt logging times must increase.")

            index = checkpoint["count"] + 1

            body = {
                "index": index,
                "previous": checkpoint["head"],
                "receipt": receipt,
            }
            entry = {
                "body": body,
                "sha256": digest(body),
            }

            path = self.directory / f"receipt_{index:08d}.json"

            if path.exists():
                # Only an exact interrupted append can be retried.
                if json.loads(path.read_text()) != entry:
                    raise ValueError(
                        "Uncommitted provenance receipt conflicts with retry."
                    )
            else:
                write_atomic(path, entry)

            checkpoint["count"] = index
            checkpoint["head"] = entry["sha256"]
            self.save_checkpoint(checkpoint)

            return checkpoint

    def verify_entries(self, checkpoint):
        if checkpoint["binding"] != self.binding:
            raise ValueError("Provenance collection binding changed.")

        previous = None
        previous_time = timestamp(self.bound_at)
        receipts = []

        for index in range(1, checkpoint["count"] + 1):
            entry, payload = self._read_entry(index)
            body = entry["body"]

            if body["previous"] != previous:
                raise ValueError("Provenance chain link mismatch.")

            recorded = timestamp(payload["recorded_at"])

            if recorded <= previous_time:
                raise ValueError("Receipt logging times must increase.")

            previous_time = recorded
            previous = entry["sha256"]
            receipts.append(body["receipt"])

        if previous != checkpoint["head"]:
            raise ValueError("Provenance chain head mismatch.")

        return receipts

    def seal(self):
        # Base seal checks the complete chain and refuses orphan entries.
        return super().seal()

    def export(self, expected_checkpoint):
        # Requires a sealed checkpoint matching a retained reference.
        return super().export(expected_checkpoint)
