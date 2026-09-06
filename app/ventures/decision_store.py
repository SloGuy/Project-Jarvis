from __future__ import annotations

import json
from pathlib import Path

from app.ventures.decision_models import VenturesDecision


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DECISION_DIRECTORY = PROJECT_ROOT / "state" / "ventures"
DECISION_FILE = DECISION_DIRECTORY / "decisions.json"


def _load_decisions() -> list[dict]:
    if not DECISION_FILE.exists():
        return []

    with DECISION_FILE.open("r", encoding="utf-8") as file:
        records = json.load(file)

    if not isinstance(records, list):
        raise ValueError("Ventures decision state must be a JSON list.")

    return records


def list_decisions(
    opportunity_id: str | None = None,
) -> list[dict]:
    records = _load_decisions()
    if opportunity_id is None:
        return records

    return [
        record
        for record in records
        if record["opportunity_id"] == opportunity_id
    ]


def save_decision(decision: VenturesDecision) -> dict:
    records = _load_decisions()
    record = decision.to_dict()
    records.append(record)

    DECISION_DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary_file = DECISION_FILE.with_suffix(".tmp")

    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump(
            records,
            file,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )

    temporary_file.replace(DECISION_FILE)
    return record
