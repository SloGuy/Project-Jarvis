"""Retry-safe automated registration without changing pinned sources.

The request marker identifies an automation request; it is not a credential.
Callers must provide the same persisted draft when retrying.
"""

import fcntl
import hashlib
from copy import deepcopy

from app.capital import validation_registry as registry
from app.capital.validation_plan import canonical


PREFIX = "capital.validation/request/"


def _find_existing(draft):
    marker = draft["created_by"]
    with registry.locked_state() as state:
        matches = [
            row for row in state["plans"].values()
            if row["envelope"]["plan"]["created_by"] == marker
        ]
        if len(matches) > 1:
            raise RuntimeError("Multiple plans share an automation request.")
        if not matches:
            return None

        row = matches[0]
        registered_draft = deepcopy(row["envelope"]["plan"])
        registered_draft.pop("created_at")

        if canonical(registered_draft) != canonical(draft):
            raise ValueError("Validation request key has different inputs.")
        return registry.copy_value(row)


def register_validation_once(draft, *, request_key):
    """Return the existing plan or register a new one.

    Reusing a request never resets plan status or authorizes execution.
    Original overlap, future-window, and current-binding checks remain
    enforced by the existing registry for new registrations.

    Registry deletion is not recoverable through this request marker.
    This is duplicate prevention, not a tamper-proof request ledger.
    """
    if (
        not isinstance(request_key, str)
        or not request_key.strip()
        or len(request_key) > 200
    ):
        raise ValueError("Invalid validation request key.")
    if not isinstance(draft, dict):
        raise ValueError("Validation draft must be a dictionary.")
    if draft.get("created_by") != "capital.validation":
        raise ValueError("Expected the Capital validation agent.")
    if "created_at" in draft:
        raise ValueError("Registration assigns the creation timestamp.")

    prepared = deepcopy(draft)
    identity = hashlib.sha256(
        request_key.strip().encode("utf-8")
    ).hexdigest()
    prepared["created_by"] = PREFIX + identity
    canonical(prepared)

    registry.DIRECTORY.mkdir(parents=True, exist_ok=True)
    lock_path = registry.DIRECTORY / "autonomy-registration.lock"

    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            existing = _find_existing(prepared)
            if existing is not None:
                return existing

            # register_plan takes its own registry lock and retains all
            # existing overlap and binding protections.
            return registry.register_plan(prepared)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
