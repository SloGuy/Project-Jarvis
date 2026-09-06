from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.ventures.underwriting_models import VenturesUnderwritingReport


PROJECT_ROOT = Path(__file__).resolve().parents[2]
UNDERWRITING_DIRECTORY = PROJECT_ROOT / "state" / "ventures"
UNDERWRITING_FILE = UNDERWRITING_DIRECTORY / "underwriting_reports.json"


def _load_raw_reports() -> list[dict]:
    if not UNDERWRITING_FILE.exists():
        return []

    with UNDERWRITING_FILE.open("r", encoding="utf-8") as file:
        reports = json.load(file)

    if not isinstance(reports, list):
        raise ValueError(
            "Ventures underwriting state must be a JSON list."
        )

    return reports


def save_underwriting_report(
    report: VenturesUnderwritingReport,
) -> dict:
    reports = _load_raw_reports()
    record = {
        "opportunity_id": report.opportunity_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "report": report.to_dict(),
    }
    reports.append(record)

    UNDERWRITING_DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary_file = UNDERWRITING_FILE.with_suffix(".tmp")

    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump(
            reports,
            file,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )

    temporary_file.replace(UNDERWRITING_FILE)
    return record


def list_underwriting_reports(
    opportunity_id: str | None = None,
) -> list[dict]:
    reports = _load_raw_reports()
    if opportunity_id is None:
        return reports

    return [
        record
        for record in reports
        if record["opportunity_id"] == opportunity_id
    ]
