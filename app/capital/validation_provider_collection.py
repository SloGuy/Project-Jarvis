"""Registry-bound provider-time collections for version-2 plans.

Legacy witness collections are not modified.
Collection files and their retained registry checkpoint belong together.
"""

from app.agents.capital_registry import VALIDATION_AGENT_ID
from app.capital import validation_registry as registry
from app.capital.autonomy_control import read_operating_policy
from app.capital.autonomy_policy import authorize_capital_action
from app.capital.observation_witness import digest
from app.capital.validation_input_contract import (
    validate_input_contract,
)
from app.capital.validation_plan import timestamp, verify_plan
from app.capital.validation_quote_store import (
    ValidationQuoteReceiptStore,
)


COLLECTION_FIELDS = {
    "schema_version",
    "kind",
    "plan_sha256",
    "bound_at",
    "status",
    "store_checkpoint",
    "sha256",
}


def authorize_collection():
    authorize_capital_action(
        agent_id=VALIDATION_AGENT_ID,
        action="capital.collect_validation",
        policy=read_operating_policy(),
        execution_mode="paper",
    )


def check_current_binding(plan):
    from app.capital.run_validation import current_binding

    return current_binding(plan)


def require_provider_plan(row):
    plan = verify_plan(
        row["envelope"],
        expected_sha256=row["registered_sha256"],
    )

    if plan["schema_version"] != 2:
        raise ValueError("Provider collection requires a version-2 plan.")

    if "witness_collection" in row:
        raise ValueError("Legacy and provider collections cannot be mixed.")

    validate_input_contract(
        plan["input_contract"],
        policy=plan["policy"],
        verify_sources=True,
    )

    return plan


def quote_directory(row):
    require_provider_plan(row)
    return (
        registry.DIRECTORY
        / "provider_quotes"
        / row["registered_sha256"]
    )


def store_for(row, collection):
    return ValidationQuoteReceiptStore(
        registry.DIRECTORY
        / "provider_collections"
        / row["registered_sha256"],
        envelope=row["envelope"],
        expected_sha256=row["registered_sha256"],
        bound_at=collection["bound_at"],
    )


def refresh_collection(collection):
    collection["sha256"] = digest({
        key: value
        for key, value in collection.items()
        if key != "sha256"
    })


def validate_provider_collection(row):
    require_provider_plan(row)
    collection = row.get("provider_collection")

    if (
        not isinstance(collection, dict)
        or set(collection) != COLLECTION_FIELDS
    ):
        raise ValueError("Invalid provider collection structure.")

    body = {
        key: value
        for key, value in collection.items()
        if key != "sha256"
    }

    if (
        type(collection["schema_version"]) is not int
        or collection["schema_version"] != 1
        or collection["kind"] != "provider_time_v1"
        or collection["plan_sha256"] != row["registered_sha256"]
        or collection["status"] not in {"collecting", "sealed"}
        or digest(body) != collection["sha256"]
    ):
        raise ValueError("Provider collection integrity mismatch.")

    store = store_for(row, collection)
    checkpoint = store.read_checkpoint()

    if checkpoint != collection["store_checkpoint"]:
        raise ValueError(
            "Provider checkpoint differs from registry; recovery required."
        )

    if checkpoint["sealed"] != (
        collection["status"] == "sealed"
    ):
        raise ValueError("Provider collection seal state differs.")

    return collection


def bind_provider_collection(plan_id):
    authorize_collection()

    with registry.locked_state(write=True) as state:
        row = state["plans"][plan_id]
        plan = require_provider_plan(row)

        if row["status"] != "registered":
            raise ValueError("Plan is not registered.")

        if "provider_collection" in row:
            validate_provider_collection(row)
            return registry.copy_value(row["provider_collection"])

        now = registry.now_utc()

        if now >= timestamp(plan["start"]):
            raise ValueError("Bind provider collection before the window.")

        check_current_binding(plan)

        collection = {
            "schema_version": 1,
            "kind": "provider_time_v1",
            "plan_sha256": row["registered_sha256"],
            "bound_at": now.isoformat(),
            "status": "collecting",
            "store_checkpoint": None,
        }

        collection["store_checkpoint"] = (
            store_for(row, collection).initialize()
        )
        refresh_collection(collection)

        authorize_collection()
        row["provider_collection"] = collection
        row["history"].append({
            "status": "provider_collection_bound",
            "at": now.isoformat(),
            "collection_sha256": collection["sha256"],
        })

        return registry.copy_value(collection)


def seal_provider_collection(plan_id):
    authorize_collection()

    with registry.locked_state(write=True) as state:
        row = state["plans"][plan_id]
        plan = require_provider_plan(row)

        if row["status"] != "registered":
            raise ValueError("Plan is not registered.")

        collection = validate_provider_collection(row)

        if registry.now_utc() < timestamp(plan["end_exclusive"]):
            raise ValueError("Collection period has not ended.")

        if collection["status"] == "sealed":
            return registry.copy_value(collection)

        authorize_collection()
        collection["store_checkpoint"] = (
            store_for(row, collection).seal()
        )
        collection["status"] = "sealed"
        refresh_collection(collection)

        row["history"].append({
            "status": "provider_collection_sealed",
            "at": registry.now_utc().isoformat(),
            "collection_sha256": collection["sha256"],
            "receipt_count": collection["store_checkpoint"]["count"],
        })

        return registry.copy_value(collection)


def materialize_provider_collection(row):
    collection = validate_provider_collection(row)

    if collection["status"] != "sealed":
        raise ValueError("Provider collection is not sealed.")

    receipts = store_for(row, collection).export(
        collection["store_checkpoint"]
    )

    return {
        "collection": registry.copy_value(collection),
        "receipts": receipts,
    }
