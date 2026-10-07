"""Independent collection cycles for automation-owned validation plans."""

from datetime import timedelta
import fcntl
import json

from app.agents.capital_registry import VALIDATION_AGENT_ID
from app.capital.autonomy_control import read_operating_policy
from app.capital.autonomy_policy import authorize_capital_action
from app.capital.autonomy_validation_registration import PREFIX
from app.capital import validation_collection as collection
from app.capital import validation_registry as registry
from app.capital.observation_witness import digest, verify_receipt
from app.capital.validation_plan import timestamp


WARMUP = timedelta(minutes=30)
CAPTURE_INTERVAL = timedelta(minutes=2)


def current_binding(plan):
    from app.capital.run_validation import (
        current_binding as check_binding,
    )

    return check_binding(plan)


def _authorize():
    authorize_capital_action(
        agent_id=VALIDATION_AGENT_ID,
        action="capital.collect_validation",
        policy=read_operating_policy(),
        execution_mode="paper",
    )


def _advance(plan_id):
    _authorize()
    row = registry.get_plan(plan_id)
    plan = row["envelope"]["plan"]
    if plan["schema_version"] == 2:
        from app.capital.validation_provider_capture import (
            capture_provider_cycle,
        )

        if (
            not plan["created_by"].startswith(PREFIX)
            or row["status"] != "registered"
        ):
            return {
                "plan_id": plan_id,
                "status": "skipped",
            }

        return capture_provider_cycle(plan_id)
    if (
        not plan["created_by"].startswith(PREFIX)
        or row["status"] != "registered"
    ):
        return {"plan_id": plan_id, "status": "skipped"}

    now = registry.now_utc()
    start = timestamp(plan["start"])
    end = timestamp(plan["end_exclusive"])

    if now < start - WARMUP:
        return {"plan_id": plan_id, "status": "waiting_for_collection_window"}

    witness = row.get("witness_collection")
    if witness is None:
        if now >= start:
            raise ValueError("Collection was not bound before the window.")
        current_binding(plan)
        _authorize()
        collection.bind(plan_id)
        return {"plan_id": plan_id, "status": "bound"}

    collection.validate_collection(witness, row["registered_sha256"])
    store = collection.store_for(witness)
    checkpoint = witness["store_checkpoint"]
    if store.read_checkpoint() != checkpoint:
        raise ValueError("Collection checkpoint requires recovery.")

    if witness["status"] == "sealed":
        return {"plan_id": plan_id, "status": "sealed"}

    current_binding(plan)
    if now >= end:
        _authorize()
        collection.seal(plan_id)
        return {"plan_id": plan_id, "status": "sealed"}

    count = checkpoint["count"]
    if count:
        path = store.directory / f"receipt_{count:08d}.json"
        entry = json.loads(path.read_text())
        if (
            digest(entry["body"]) != entry["sha256"]
            or entry["sha256"] != checkpoint["head"]
        ):
            raise ValueError("Last receipt integrity mismatch.")
        payload = verify_receipt(entry["body"]["receipt"])
        previous = timestamp(payload["witnessed_at"])
        if previous <= timestamp(witness["bound_at"]) or previous > now:
            raise ValueError("Last receipt timestamp is invalid.")
        if now - previous < CAPTURE_INTERVAL:
            return {
                "plan_id": plan_id,
                "status": "waiting_for_capture",
                "receipt_count": count,
            }

    _authorize()
    count = collection.capture_next(plan_id)
    return {
        "plan_id": plan_id,
        "status": "captured",
        "receipt_count": count,
    }


def run_collection_cycle():
    """Serialize collection dispatch independently of model work.

    Existing pinned-source and receipt checks remain authoritative.
    Mismatches are reported rather than repaired or bypassed here.
    A problem with one plan does not prevent checking other plans.
    """
    if not read_operating_policy().enabled:
        return {"status": "disabled", "outcomes": []}

    registry.DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = registry.DIRECTORY / "autonomy-collection.lock"

    with path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy", "outcomes": []}

        try:
            _authorize()
            with registry.locked_state() as state:
                plan_ids = sorted(
                    plan_id for plan_id, row in state["plans"].items()
                    if (
                        row["status"] == "registered"
                        and row["envelope"]["plan"]["created_by"].startswith(
                            PREFIX
                        )
                    )
                )

            outcomes = []
            for plan_id in plan_ids:
                try:
                    outcomes.append(_advance(plan_id))
                except Exception as error:
                    outcomes.append({
                        "plan_id": plan_id,
                        "status": "blocked",
                        "error_type": type(error).__name__,
                        "reason": str(error),
                    })

            return {
                "status": (
                    "blocked"
                    if any(row["status"] == "blocked" for row in outcomes)
                    else "success"
                ),
                "outcomes": outcomes,
                "live_capital_authorized": False,
            }
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    result = run_collection_cycle()
    print(json.dumps(result, indent=2), flush=True)
    raise SystemExit(1 if result["status"] == "blocked" else 0)
