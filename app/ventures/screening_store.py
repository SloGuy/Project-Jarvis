from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.ventures.screening_models import ScreeningScore


PROJECT_ROOT = Path(__file__).resolve().parents[2]

SCREENING_DIRECTORY = (
    PROJECT_ROOT
    / "state"
    / "ventures"
)

SCREENING_FILE = (
    SCREENING_DIRECTORY
    / "screenings.json"
)


def _utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _ensure_directory() -> None:
    SCREENING_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )


def _load_raw_screenings() -> list[dict]:
    _ensure_directory()

    if not SCREENING_FILE.exists():
        return []

    with SCREENING_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError(
            "Ventures screenings state must be a JSON list."
        )

    return data


def _save_raw_screenings(
    records: list[dict],
) -> None:
    _ensure_directory()

    temporary_file = SCREENING_FILE.with_suffix(
        ".tmp"
    )

    with temporary_file.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            records,
            file,
            indent=2,
            sort_keys=True,
        )

    temporary_file.replace(
        SCREENING_FILE
    )


def save_screening(
    *,
    opportunity_id: str,
    result: ScreeningScore,
) -> dict:
    records = _load_raw_screenings()

    record = {
        "opportunity_id": opportunity_id,
        "screened_at": _utc_now_iso(),
        "result": result.to_dict(),
    }

    records.append(record)

    _save_raw_screenings(records)

    return record


def list_screenings(
    opportunity_id: str | None = None,
) -> list[dict]:
    records = _load_raw_screenings()

    if opportunity_id is None:
        return records

    return [
        record
        for record in records
        if record["opportunity_id"]
        == opportunity_id
    ]
