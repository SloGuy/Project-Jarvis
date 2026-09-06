from __future__ import annotations

import fcntl
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DISCOVERY_DIRECTORY = PROJECT_ROOT / "state" / "ventures" / "discovery"


@contextmanager
def discovery_run_lock():
    DISCOVERY_DIRECTORY.mkdir(parents=True, exist_ok=True)
    lock_path = DISCOVERY_DIRECTORY / "collector.lock"

    with lock_path.open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(
                lock.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as exc:
            raise RuntimeError(
                "Another Ventures discovery run is already active."
            ) from exc

        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def save_discovery_run(
    *,
    status: str,
    started_at: str,
    source: str,
    snapshot: dict | None,
    intake_results: list[dict],
    error: str | None = None,
) -> dict:
    if status not in {"success", "failed"}:
        raise ValueError("Invalid discovery run status.")
    if status == "success" and (snapshot is None or error is not None):
        raise ValueError("Successful runs require a snapshot and no error.")
    if status == "failed" and not error:
        raise ValueError("Failed runs require an error description.")

    record = {
        "run_id": f"discovery_{uuid.uuid4().hex}",
        "source": source,
        "status": status,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": snapshot,
        "intake_results": intake_results,
        "error": error,
        "automatic_offer_authority": False,
        "automatic_purchase_authority": False,
        "capital_transfer_authority": False,
    }

    # Validate serialization before creating a temporary file.
    payload = json.dumps(
        record,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )

    runs_directory = DISCOVERY_DIRECTORY / "runs"
    runs_directory.mkdir(parents=True, exist_ok=True)
    destination = runs_directory / f"{record['run_id']}.json"

    descriptor, temporary_name = tempfile.mkstemp(
        prefix="discovery_",
        suffix=".tmp",
        dir=runs_directory,
    )
    temporary_path = Path(temporary_name)

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())

        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)

    return record


def list_discovery_runs() -> list[dict]:
    runs_directory = DISCOVERY_DIRECTORY / "runs"
    if not runs_directory.exists():
        return []

    records = []
    for path in runs_directory.glob("discovery_*.json"):
        with path.open("r", encoding="utf-8") as file:
            record = json.load(file)

        if not isinstance(record, dict):
            raise ValueError(f"Invalid discovery run: {path.name}")

        records.append(record)

    return sorted(
        records,
        key=lambda record: (
            record["finished_at"],
            record["run_id"],
        ),
    )
