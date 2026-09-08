"""Plan-bound visibility collection stored in the atomic registry."""
import argparse
import json

from app.capital import validation_registry as registry
from app.capital.observation_witness import (
    capture, digest, timestamp, verify_receipt,
)

MAX_RECEIPTS = 10000


def validate_collection(collection, plan_sha256):
    if set(collection) != {
        "schema_version", "plan_sha256", "bound_at", "status",
        "receipts", "sha256",
    }:
        raise ValueError("Invalid collection structure.")
    if (
        collection["schema_version"] != 1
        or collection["plan_sha256"] != plan_sha256
        or collection["status"] not in {"collecting", "sealed"}
        or not isinstance(collection["receipts"], list)
        or len(collection["receipts"]) > MAX_RECEIPTS
    ):
        raise ValueError("Invalid collection binding or state.")
    body = {k: v for k, v in collection.items() if k != "sha256"}
    if digest(body) != collection["sha256"]:
        raise ValueError("Collection integrity mismatch.")
    previous = timestamp(collection["bound_at"])
    for receipt in collection["receipts"]:
        payload = verify_receipt(receipt)
        current = timestamp(payload["witnessed_at"])
        if current <= previous:
            raise ValueError("Witness times must increase.")
        previous = current
    return collection


def refresh(collection):
    collection["sha256"] = digest({
        k: v for k, v in collection.items() if k != "sha256"
    })


def bind(plan_id):
    with registry.locked_state(write=True) as state:
        row = state["plans"][plan_id]
        now = registry.now_utc()
        plan = row["envelope"]["plan"]
        if row["status"] != "registered":
            raise ValueError("Plan is not registered.")
        if now >= timestamp(plan["start"]):
            raise ValueError("Bind collection before the period starts.")
        if "witness_collection" in row:
            validate_collection(
                row["witness_collection"], row["registered_sha256"]
            )
            return row["witness_collection"]["sha256"]
        collection = {
            "schema_version": 1,
            "plan_sha256": row["registered_sha256"],
            "bound_at": now.isoformat(),
            "status": "collecting",
            "receipts": [],
        }
        refresh(collection)
        row["witness_collection"] = collection
        row["history"].append({
            "status": "collection_bound",
            "at": now.isoformat(),
            "collection_sha256": collection["sha256"],
        })
    return collection["sha256"]


def capture_next(plan_id):
    # Keep the registry lock through capture and append. A completed
    # capture that fails to append is not part of this collection.
    with registry.locked_state(write=True) as state:
        row = state["plans"][plan_id]
        plan = row["envelope"]["plan"]
        collection = validate_collection(
            row["witness_collection"], row["registered_sha256"]
        )
        if row["status"] != "registered" or collection["status"] != "collecting":
            raise ValueError("Collection is not open.")
        if registry.now_utc() >= timestamp(plan["end_exclusive"]):
            raise ValueError("Collection period has ended.")
        if len(collection["receipts"]) >= MAX_RECEIPTS:
            raise ValueError("Receipt limit reached.")

        path = capture(plan["asset_id"], plan["provider"], limit=60)
        receipt = json.loads(path.read_text())
        payload = verify_receipt(receipt)
        witnessed = timestamp(payload["witnessed_at"])
        if not (
            timestamp(collection["bound_at"]) < witnessed
            < timestamp(plan["end_exclusive"])
        ):
            raise ValueError("Witness is outside collection interval.")
        if witnessed > registry.now_utc():
            raise ValueError("Witness clock is ahead of registry clock.")
        for observation in payload["observations"]:
            if (
                observation["asset_id"] != plan["asset_id"]
                or observation["provider"] != plan["provider"]
            ):
                raise ValueError("Receipt series differs from plan.")
        collection["receipts"].append(receipt)
        refresh(collection)
        validate_collection(collection, row["registered_sha256"])
        count = len(collection["receipts"])
    return count


def seal(plan_id):
    with registry.locked_state(write=True) as state:
        row = state["plans"][plan_id]
        plan = row["envelope"]["plan"]
        collection = validate_collection(
            row["witness_collection"], row["registered_sha256"]
        )
        if row["status"] != "registered":
            raise ValueError("Plan is not registered.")
        now = registry.now_utc()
        if now < timestamp(plan["end_exclusive"]):
            raise ValueError("Collection period has not ended.")
        if not collection["receipts"]:
            raise ValueError("Cannot seal an empty collection.")
        if collection["status"] == "sealed":
            return collection["sha256"]
        collection["status"] = "sealed"
        refresh(collection)
        row["history"].append({
            "status": "collection_sealed",
            "at": now.isoformat(),
            "collection_sha256": collection["sha256"],
            "receipt_count": len(collection["receipts"]),
        })
    return collection["sha256"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["bind", "capture", "seal"])
    parser.add_argument("plan_id")
    args = parser.parse_args()
    function = {"bind": bind, "capture": capture_next, "seal": seal}[args.command]
    print(args.command, function(args.plan_id))


if __name__ == "__main__":
    main()
