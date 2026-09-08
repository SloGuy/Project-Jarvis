"""Recover a single interrupted receipt-store/registry transition."""
import argparse
import json

from app.capital import validation_registry as registry
from app.capital.validation_collection import (
    validate_collection, store_for, refresh, MAX_RECEIPTS,
)
from app.capital.observation_witness import digest, timestamp


def recover(plan_id):
    with registry.locked_state(write=True) as state:
        row = state["plans"][plan_id]
        if row["status"] != "registered":
            raise ValueError("Recovery requires an unclaimed registered plan.")
        collection = registry.copy_value(row["witness_collection"])
        validate_collection(collection, row["registered_sha256"])
        expected = collection["store_checkpoint"]
        store = store_for(collection)

        with store.lock():
            actual = store.read_checkpoint()
            if actual["binding"] != expected["binding"]:
                raise ValueError("Store binding differs from registry.")

            # Verify the retained prefix even if the store moved ahead.
            store.verify_entries(expected)
            desired = registry.copy_value(actual)
            orphan = store.directory / (
                f"receipt_{actual['count'] + 1:08d}.json"
            )

            if actual == expected and orphan.exists():
                if actual["sealed"]:
                    raise ValueError("Unexpected receipt after sealing.")
                entry = json.loads(orphan.read_text())
                body = entry["body"]
                if (
                    set(entry) != {"body", "sha256"}
                    or set(body) != {"index", "previous", "receipt"}
                    or body["index"] != actual["count"] + 1
                    or body["previous"] != actual["head"]
                    or digest(body) != entry["sha256"]
                ):
                    raise ValueError("Interrupted receipt integrity mismatch.")
                desired["count"] += 1
                desired["head"] = entry["sha256"]
                desired["sha256"] = digest({
                    k: v for k, v in desired.items() if k != "sha256"
                })
            elif actual == expected:
                return "already_consistent"
            elif not (
                not expected["sealed"]
                and (
                    (
                        actual["count"] == expected["count"] + 1
                        and not actual["sealed"]
                    )
                    or (
                        actual["count"] == expected["count"]
                        and actual["head"] == expected["head"]
                        and actual["sealed"]
                    )
                )
            ):
                raise ValueError("Store differs by more than one supported transition.")

            if desired["count"] > MAX_RECEIPTS:
                raise ValueError("Receipt limit exceeded.")
            extra = store.directory / (
                f"receipt_{desired['count'] + 1:08d}.json"
            )
            if extra.exists():
                raise ValueError("Multiple pending transitions require investigation.")

            receipts = store.verify_entries(desired)
            plan = row["envelope"]["plan"]
            now = registry.now_utc()
            for receipt in receipts:
                payload = receipt["payload"]
                witnessed = timestamp(payload["witnessed_at"])
                if not (
                    timestamp(collection["bound_at"]) < witnessed
                    < timestamp(plan["end_exclusive"])
                    and witnessed <= now
                ):
                    raise ValueError("Recovered witness is outside its allowed interval.")
                if any(
                    item["asset_id"] != plan["asset_id"]
                    or item["provider"] != plan["provider"]
                    for item in payload["observations"]
                ):
                    raise ValueError("Recovered observation series differs from plan.")

            if desired["sealed"] and now < timestamp(plan["end_exclusive"]):
                raise ValueError("Cannot recover sealing before the period ends.")

            collection["store_checkpoint"] = desired
            collection["status"] = (
                "sealed" if desired["sealed"] else "collecting"
            )
            # Check receipt ordering as well as the complete hash chain.
            expanded = registry.copy_value(collection)
            expanded["receipts"] = receipts
            refresh(expanded)
            validate_collection(expanded, row["registered_sha256"])
            refresh(collection)

            if desired != actual:
                store.save_checkpoint(desired)

        row["witness_collection"] = collection
        row["history"].append({
            "status": "collection_recovered",
            "at": registry.now_utc().isoformat(),
            "collection_sha256": collection["sha256"],
            "receipt_count": desired["count"],
            "sealed": desired["sealed"],
        })
    return "recovered"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("plan_id")
    args = parser.parse_args()
    print(recover(args.plan_id))


if __name__ == "__main__":
    main()
