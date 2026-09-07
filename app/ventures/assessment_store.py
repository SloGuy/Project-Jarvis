from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.ventures.assessment_interviews import current_interview_inputs

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSESSMENT_DIRECTORY = PROJECT_ROOT / "state" / "ventures" / "assessments"


def assessment_key(
    *,
    research_record: dict,
    configuration: dict,
    interview_inputs: list[dict] | None = None,
) -> str:
    if interview_inputs is None:
        interview_inputs = current_interview_inputs(
            research_record["opportunity_id"]
        )

    payload = json.dumps(
        {
            "research_record": research_record,
            "configuration": configuration,
            "interview_inputs": interview_inputs,
        },
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _record_path(key: str) -> Path:
    if (
        not isinstance(key, str)
        or len(key) != 64
        or any(character not in "0123456789abcdef" for character in key)
    ):
        raise ValueError("Invalid assessment key.")
    return ASSESSMENT_DIRECTORY / f"assessment_{key}.json"


def get_assessment(key: str) -> dict | None:
    path = _record_path(key)
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as file:
        record = json.load(file)

    if not isinstance(record, dict) or record.get("assessment_key") != key:
        raise ValueError("Invalid stored assessment.")
    return record


def save_assessment(
    *,
    research_record: dict,
    configuration: dict,
    result: dict,
    interview_inputs: list[dict] | None = None,
) -> dict:
    if interview_inputs is None:
        interview_inputs = current_interview_inputs(
            research_record["opportunity_id"]
        )
    opportunity_id = research_record["opportunity_id"]
    if (
        research_record["report"]["opportunity_id"] != opportunity_id
        or result["opportunity_id"] != opportunity_id
        or result["research_created_at"] != research_record["created_at"]
    ):
        raise ValueError("Assessment does not match its research input.")
    if result["model"] != configuration["model"]:
        raise ValueError("Assessment model does not match configuration.")
    if result.get("review_status") != "unreviewed_model_draft":
        raise ValueError("Only unreviewed model drafts may be saved here.")

    key = assessment_key(
        research_record=research_record,
        configuration=configuration,
        interview_inputs=interview_inputs,
    )
    destination = _record_path(key)
    record = {
        "assessment_key": key,
        "opportunity_id": opportunity_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "research_created_at": research_record["created_at"],
        "research_snapshot": research_record,
        "interview_inputs": interview_inputs,
        "configuration": configuration,
        "result": result,
    }
    payload = json.dumps(
        record,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    )

    ASSESSMENT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    with (ASSESSMENT_DIRECTORY / "store.lock").open(
        "a", encoding="utf-8"
    ) as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            existing = get_assessment(key)
            if existing is not None:
                return existing

            descriptor, name = tempfile.mkstemp(
                prefix="assessment_",
                suffix=".tmp",
                dir=ASSESSMENT_DIRECTORY,
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

            return record
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def list_assessments(
    opportunity_id: str | None = None,
) -> list[dict]:
    if not ASSESSMENT_DIRECTORY.exists():
        return []

    records = []
    for path in ASSESSMENT_DIRECTORY.glob("assessment_*.json"):
        key = path.stem.removeprefix("assessment_")
        record = get_assessment(key)
        if record is None:
            continue
        if (
            opportunity_id is None
            or record["opportunity_id"] == opportunity_id
        ):
            records.append(record)

    return sorted(records, key=lambda record: (
        record["created_at"], record["assessment_key"]
    ))
