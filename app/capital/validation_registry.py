"""Atomic local registration and lifecycle tracking for validation plans."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from app.capital.validation_plan import seal_plan, timestamp, verify_plan

DIRECTORY = Path(__file__).resolve().parents[2] / "runtime/capital/validation"
STATUSES = {"registered", "running", "completed", "failed"}


def now_utc():
    return datetime.now(timezone.utc)


def check_state(state):
    if (
        not isinstance(state, dict)
        or state.get("schema_version") != 1
        or not isinstance(state.get("plans"), dict)
    ):
        raise ValueError("Invalid validation registry.")
    for key, row in state["plans"].items():
        if (
            not isinstance(row, dict)
            or row.get("plan_id") != key
            or row.get("status") not in STATUSES
            or not isinstance(row.get("history"), list)
            or not row["history"]
        ):
            raise ValueError("Invalid validation registry entry.")
        verify_plan(row["envelope"], expected_sha256=row["registered_sha256"])
    return state


def save_state(state):
    check_state(state)
    descriptor, name = tempfile.mkstemp(
        prefix="registry_", suffix=".json", dir=DIRECTORY
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, DIRECTORY / "registry.json")
        directory_fd = os.open(DIRECTORY, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def locked_state(write=False):
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    with (DIRECTORY / "registry.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        try:
            path = DIRECTORY / "registry.json"
            try:
                state = check_state(json.loads(path.read_text()))
            except FileNotFoundError:
                state = {"schema_version": 1, "plans": {}}
            except (ValueError, KeyError, TypeError, OSError) as error:
                raise RuntimeError(
                    "Validation registry cannot be read safely; writes blocked."
                ) from error
            yield state
            if write:
                save_state(state)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def copy_value(value):
    return json.loads(json.dumps(value))


def register_plan(draft):
    from app.capital.run_validation import current_binding
    return _register_plan(draft, validate_binding=current_binding)


def _register_plan(draft, *, validate_binding):
    with locked_state(write=True) as state:
        # Creation time is assigned inside the lock, not accepted from callers.
        envelope = seal_plan(draft, now=now_utc())
        plan = envelope["plan"]
        validate_binding(plan)
        start, end = timestamp(plan["start"]), timestamp(plan["end_exclusive"])
        for row in state["plans"].values():
            existing = row["envelope"]["plan"]
            same_series = (
                existing["asset_id"] == plan["asset_id"]
                and existing["provider"] == plan["provider"]
            )
            overlaps = (
                start < timestamp(existing["end_exclusive"])
                and timestamp(existing["start"]) < end
            )
            if same_series and overlaps:
                raise ValueError(
                    "This asset/provider period is already reserved. "
                    "Failed and completed plans retain their reservations."
                )
        plan_id = f"validation_{uuid4().hex}"
        row = {
            "plan_id": plan_id,
            "envelope": envelope,
            "registered_sha256": envelope["sha256"],
            "status": "registered",
            "history": [{
                "status": "registered",
                "at": plan["created_at"],
            }],
        }
        state["plans"][plan_id] = row
    return copy_value(row)


def get_plan(plan_id):
    with locked_state() as state:
        if plan_id not in state["plans"]:
            raise KeyError(f"Unknown validation plan: {plan_id}")
        return copy_value(state["plans"][plan_id])


def claim_plan(plan_id):
    with locked_state(write=True) as state:
        row = state["plans"][plan_id]

        if row["status"] != "registered":
            raise ValueError("Plan has already been claimed.")

        now = now_utc()
        plan = row["envelope"]["plan"]

        if now < timestamp(plan["end_exclusive"]):
            raise ValueError("Evaluation period has not ended.")

        if plan["schema_version"] == 2:
            from app.capital.validation_provider_collection import (
                materialize_provider_collection,
            )

            # Verify retained checkpoint, complete receipt chain,
            # plan bindings, source fingerprints, and sealed state.
            materialize_provider_collection(row)

        else:
            if "provider_collection" in row:
                raise ValueError(
                    "Legacy plan cannot claim provider-time evidence."
                )

            if "witness_collection" in row:
                from app.capital.validation_collection import (
                    validate_collection,
                )

                collection = validate_collection(
                    row["witness_collection"],
                    row["registered_sha256"],
                )

                if collection["status"] != "sealed":
                    raise ValueError(
                        "Witness collection must be sealed before claiming."
                    )

        token = uuid4().hex
        row["status"] = "running"
        row["run_token"] = token
        row["history"].append({
            "status": "running",
            "at": now.isoformat(),
        })

    return copy_value(row)


def finish_plan(plan_id, run_token, *, succeeded, detail):
    if type(succeeded) is not bool:
        raise ValueError("Success must be a boolean.")
    if not isinstance(detail, str) or not detail.strip():
        raise ValueError("A result reference or failure explanation is required.")
    with locked_state(write=True) as state:
        row = state["plans"][plan_id]
        if row["status"] != "running" or row.get("run_token") != run_token:
            raise ValueError("Run ownership or state mismatch.")
        row["status"] = "completed" if succeeded else "failed"
        row["history"].append({
            "status": row["status"],
            "at": now_utc().isoformat(),
            "detail": detail.strip(),
        })
    return copy_value(row)
