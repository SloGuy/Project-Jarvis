from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ATTEMPT_DIRECTORY = (
    PROJECT_ROOT / "state" / "ventures" / "assessment_attempts"
)


def _path(attempt_id: str) -> Path:
    if (
        not isinstance(attempt_id, str)
        or len(attempt_id) != 32
        or any(character not in "0123456789abcdef"
               for character in attempt_id)
    ):
        raise ValueError("Invalid attempt ID.")
    return ATTEMPT_DIRECTORY / f"attempt_{attempt_id}.json"


def _save(record: dict) -> None:
    payload = json.dumps(
        record, indent=2, sort_keys=True, allow_nan=False
    )
    destination = _path(record["attempt_id"])
    ATTEMPT_DIRECTORY.mkdir(parents=True, exist_ok=True)

    descriptor, name = tempfile.mkstemp(
        prefix="attempt_", suffix=".tmp", dir=ATTEMPT_DIRECTORY
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def start_attempt(
    *,
    opportunity_id: str,
    assessment_key: str,
) -> dict:
    if not opportunity_id or not assessment_key:
        raise ValueError("Opportunity ID and assessment key are required.")

    record = {
        "attempt_id": uuid.uuid4().hex,
        "opportunity_id": opportunity_id,
        "assessment_key": assessment_key,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "status": "running",
        "error": None,
    }
    _save(record)
    return record


def finish_attempt(
    attempt: dict,
    *,
    status: str,
    error: str | None = None,
) -> dict:
    if status not in {"created", "cached", "failed", "deferred"}:
        raise ValueError("Invalid assessment attempt outcome.")
    if status == "failed" and not error:
        raise ValueError("Failed attempts require an error.")
    if status in {"created", "cached"} and error is not None:
        raise ValueError("Successful attempts cannot contain an error.")

    record = dict(
        attempt,
        status=status,
        finished_at=datetime.now(timezone.utc).isoformat(),
        error=error,
    )
    _save(record)
    return record


def list_attempts() -> list[dict]:
    if not ATTEMPT_DIRECTORY.exists():
        return []

    records = []
    for path in ATTEMPT_DIRECTORY.glob("attempt_*.json"):
        with path.open("r", encoding="utf-8") as file:
            record = json.load(file)
        if not isinstance(record, dict):
            raise ValueError("Invalid assessment attempt record.")
        if _path(record["attempt_id"]) != path:
            raise ValueError("Assessment attempt filename mismatch.")
        records.append(record)

    return sorted(
        records,
        key=lambda record: (record["started_at"], record["attempt_id"]),
    )
