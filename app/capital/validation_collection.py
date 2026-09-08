"""Plan-bound visibility collection stored in the atomic registry."""
import argparse
import json

from app.capital import validation_registry as registry
from app.capital.observation_witness import (
    capture, digest, timestamp, verify_receipt,
)

MAX_RECEIPTS = 10000

SOURCE_FILES = (
    "app/capital/observation_witness.py",
    "app/capital/receipt_store.py",
    "app/capital/witnessed_history.py",
    "app/capital/witness_verification.py",
    "app/capital/validation_collection.py",
    "app/capital/validation_registry.py",
    "app/capital/validation_access.py",
    "app/capital/validation_plan.py",
    "app/capital/run_evaluation.py",
    "app/capital/run_validation.py",
    "app/capital/offline_verification.py",
    "app/capital/replay_analysis.py",
    "app/capital/validation_assessment.py",
)


def source_manifest():
    import hashlib
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in SOURCE_FILES
    }



def validate_collection(collection, plan_sha256):
    from app.capital.receipt_store import ReceiptStore
    required = {
        "schema_version", "plan_sha256", "bound_at", "status",
        "store_checkpoint", "sha256", "input_source_sha256",
    }
    if set(collection) not in (required, required | {"receipts"}):
        raise ValueError("Collection structure or integrity mismatch.")
    body = {k: v for k, v in collection.items() if k != "sha256"}
    if (
        collection["schema_version"] != 1
        or collection["plan_sha256"] != plan_sha256
        or collection["status"] not in {"collecting", "sealed"}
        or digest(body) != collection["sha256"]
    ):
        raise ValueError("Collection integrity mismatch.")
    if collection["input_source_sha256"] != source_manifest():
        raise ValueError("Witness processing source changed since binding.")

    checkpoint = collection["store_checkpoint"]
    checkpoint_body = {
        k: v for k, v in checkpoint.items() if k != "sha256"
    }
    if (
        set(checkpoint) != {
            "schema_version", "binding", "count", "head", "sealed", "sha256"
        }
        or checkpoint["schema_version"] != 1
        or type(checkpoint["count"]) is not int
        or not 0 <= checkpoint["count"] <= MAX_RECEIPTS
        or type(checkpoint["sealed"]) is not bool
        or checkpoint["sealed"] != (collection["status"] == "sealed")
        or digest(checkpoint_body) != checkpoint["sha256"]
        or checkpoint["binding"] != {
            "plan_sha256": plan_sha256,
            "bound_at": collection["bound_at"],
            "input_source_sha256": collection["input_source_sha256"],
        }
    ):
        raise ValueError("Collection checkpoint integrity mismatch.")

    if "receipts" in collection:
        receipts = collection["receipts"]
        if not isinstance(receipts, list) or len(receipts) != checkpoint["count"]:
            raise ValueError("Collection receipt count mismatch.")
        previous = None
        previous_time = timestamp(collection["bound_at"])
        for index, receipt in enumerate(receipts, 1):
            payload = verify_receipt(receipt)
            when = timestamp(payload["witnessed_at"])
            if when <= previous_time:
                raise ValueError("Witness times must increase.")
            previous_time = when
            previous = digest({
                "index": index,
                "previous": previous,
                "receipt": receipt,
            })
        if previous != checkpoint["head"]:
            raise ValueError("Collection receipt chain mismatch.")
    return collection


def store_for(collection):
    from app.capital.receipt_store import ReceiptStore
    key = collection["plan_sha256"]
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("Invalid collection storage key.")
    return ReceiptStore(registry.DIRECTORY / "collections" / key)


def materialize_collection(collection):
    """Read the whole sealed collection once for the evaluation packet."""
    validate_collection(collection, collection["plan_sha256"])
    if collection["status"] != "sealed":
        raise ValueError("Collection is not sealed.")
    if "receipts" in collection:
        return collection
    value = registry.copy_value(collection)
    value["receipts"] = store_for(value).export(value["store_checkpoint"])
    refresh(value)
    validate_collection(value, value["plan_sha256"])
    return value


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
            "input_source_sha256": source_manifest(),
            "status": "collecting",
            "store_checkpoint": None,
        }
        collection["store_checkpoint"] = store_for(collection).initialize({
            "plan_sha256": collection["plan_sha256"],
            "bound_at": collection["bound_at"],
            "input_source_sha256": collection["input_source_sha256"],
        })
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
        if collection["store_checkpoint"]["count"] >= MAX_RECEIPTS:
            raise ValueError("Receipt limit reached.")

        store = store_for(collection)
        if store.read_checkpoint() != collection["store_checkpoint"]:
            raise ValueError("Store and registry differ; recovery is required.")
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
        checkpoint = collection["store_checkpoint"]
        if checkpoint["count"]:
            previous_path = store.directory / (
                f"receipt_{checkpoint['count']:08d}.json"
            )
            previous = json.loads(previous_path.read_text())
            if (
                digest(previous["body"]) != previous["sha256"]
                or previous["sha256"] != checkpoint["head"]
            ):
                raise ValueError("Last receipt integrity mismatch.")
            previous_time = timestamp(
                previous["body"]["receipt"]["payload"]["witnessed_at"]
            )
            if witnessed <= previous_time:
                raise ValueError("Witness times must increase.")
        collection["store_checkpoint"] = store.append(receipt)
        refresh(collection)
        validate_collection(collection, row["registered_sha256"])
        count = collection["store_checkpoint"]["count"]
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
        if not collection["store_checkpoint"]["count"]:
            raise ValueError("Cannot seal an empty collection.")
        if collection["status"] == "sealed":
            return collection["sha256"]
        store = store_for(collection)
        if store.read_checkpoint() != collection["store_checkpoint"]:
            raise ValueError("Store and registry differ; recovery is required.")
        collection["store_checkpoint"] = store.seal()
        collection["status"] = "sealed"
        refresh(collection)
        row["history"].append({
            "status": "collection_sealed",
            "at": now.isoformat(),
            "collection_sha256": collection["sha256"],
            "receipt_count": collection["store_checkpoint"]["count"],
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
