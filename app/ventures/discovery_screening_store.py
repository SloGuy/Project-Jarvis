from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCREENING_STATE_DIRECTORY = PROJECT_ROOT / "state" / "ventures"
SCREENING_STATE_FILE = (
    SCREENING_STATE_DIRECTORY / "discovery_screening.json"
)


def _load_state() -> dict:
    if not SCREENING_STATE_FILE.exists():
        return {"evaluations": [], "queue": {}}

    with SCREENING_STATE_FILE.open("r", encoding="utf-8") as file:
        state = json.load(file)

    if (
        not isinstance(state, dict)
        or not isinstance(state.get("evaluations"), list)
        or not isinstance(state.get("queue"), dict)
    ):
        raise ValueError("Invalid discovery screening state.")

    return state


def _save_state(state: dict) -> None:
    payload = json.dumps(
        state, indent=2, sort_keys=True, allow_nan=False
    )
    descriptor, name = tempfile.mkstemp(
        prefix="discovery_screening_",
        suffix=".tmp",
        dir=SCREENING_STATE_DIRECTORY,
    )
    temporary = Path(name)

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(SCREENING_STATE_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def save_evaluation(
    *,
    opportunity_snapshot: dict,
    evaluation: dict,
    discovery_run_id: str,
) -> dict:
    opportunity_id = opportunity_snapshot["opportunity_id"]
    if evaluation["opportunity_id"] != opportunity_id:
        raise ValueError("Evaluation belongs to another opportunity.")

    # Lifecycle timestamps do not change the screening economics.
    fingerprint_inputs = {
        key: value
        for key, value in opportunity_snapshot.items()
        if key not in {"status", "created_at", "updated_at"}
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "inputs": fingerprint_inputs,
                "evaluation": evaluation,
            },
            sort_keys=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()

    SCREENING_STATE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    lock_path = SCREENING_STATE_FILE.with_suffix(".lock")

    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            state = _load_state()
            previous = next(
                (
                    record
                    for record in reversed(state["evaluations"])
                    if record["opportunity_id"] == opportunity_id
                ),
                None,
            )
            now = datetime.now(timezone.utc).isoformat()
            changed = (
                previous is None
                or previous["fingerprint"] != fingerprint
            )

            if changed:
                record = {
                    "opportunity_id": opportunity_id,
                    "fingerprint": fingerprint,
                    "evaluated_at": now,
                    "discovery_run_id": discovery_run_id,
                    "opportunity_snapshot": opportunity_snapshot,
                    "evaluation": evaluation,
                }
                state["evaluations"].append(record)
            else:
                record = previous

            queue = state["queue"]
            entry = queue.get(opportunity_id)

            # Preserve existing human/lifecycle decisions.
            eligible = (
                evaluation["research_candidate"]
                and opportunity_snapshot["status"]
                in {"discovered", "screening", "research"}
            )

            if eligible:
                if entry is None:
                    entry = {
                        "opportunity_id": opportunity_id,
                        "queued_at": now,
                        "status": "pending",
                    }
                    queue[opportunity_id] = entry
                elif entry["status"] == "inactive":
                    entry["status"] = "pending"

                entry.pop("inactive_reason", None)
                entry.pop("reconciled_at", None)

                entry.update({
                    "evaluation_fingerprint": fingerprint,
                    "last_seen_at": now,
                    "discovery_run_id": discovery_run_id,
                })
            elif entry is not None:
                entry.update({
                    "status": "inactive",
                    "evaluation_fingerprint": fingerprint,
                    "last_seen_at": now,
                    "discovery_run_id": discovery_run_id,
                })

            _save_state(state)
            return {
                "opportunity_id": opportunity_id,
                "evaluation_changed": changed,
                "disposition": evaluation["disposition"],
                "queue_status": (
                    entry["status"] if entry is not None else None
                ),
            }
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def list_discovery_evaluations(
    opportunity_id: str | None = None,
) -> list[dict]:
    records = _load_state()["evaluations"]
    if opportunity_id is None:
        return records
    return [
        record for record in records
        if record["opportunity_id"] == opportunity_id
    ]


def list_research_queue() -> list[dict]:
    return sorted(
        _load_state()["queue"].values(),
        key=lambda entry: (entry["queued_at"], entry["opportunity_id"]),
    )


def reconcile_research_queue(
    *,
    source: str,
    active_opportunity_ids: set[str],
    discovery_run_id: str,
) -> int:
    SCREENING_STATE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    lock_path = SCREENING_STATE_FILE.with_suffix(".lock")

    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            state = _load_state()
            latest = {
                record["opportunity_id"]: record
                for record in state["evaluations"]
            }
            now = datetime.now(timezone.utc).isoformat()
            deactivated = 0

            for opportunity_id, entry in state["queue"].items():
                record = latest.get(opportunity_id)
                if record is None:
                    continue

                opportunity_source = (
                    record["opportunity_snapshot"].get("source") or ""
                )
                if not opportunity_source.startswith(source + ":"):
                    continue
                if opportunity_id in active_opportunity_ids:
                    continue
                if entry["status"] == "inactive":
                    continue

                entry.update({
                    "status": "inactive",
                    "inactive_reason": (
                        "Not linked in the latest successful source scan."
                    ),
                    "reconciled_at": now,
                    "discovery_run_id": discovery_run_id,
                })
                deactivated += 1

            if deactivated:
                _save_state(state)

            return deactivated
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
