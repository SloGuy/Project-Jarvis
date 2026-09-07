from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import fcntl
from contextlib import contextmanager
from threading import RLock, local

from app.ventures.research_models import VenturesResearchReport


PROJECT_ROOT = Path(__file__).resolve().parents[2]

RESEARCH_DIRECTORY = (
    PROJECT_ROOT
    / "state"
    / "ventures"
)

RESEARCH_FILE = (
    RESEARCH_DIRECTORY
    / "research_reports.json"
)


_RESEARCH_THREAD_LOCK = RLock()
_RESEARCH_LOCK_CONTEXT = local()


@contextmanager
def research_write_lock():
    with _RESEARCH_THREAD_LOCK:
        depth = getattr(_RESEARCH_LOCK_CONTEXT, "depth", 0)

        if depth:
            _RESEARCH_LOCK_CONTEXT.depth = depth + 1
            try:
                yield
            finally:
                _RESEARCH_LOCK_CONTEXT.depth = depth
            return

        _ensure_directory()
        lock_path = RESEARCH_FILE.with_suffix(".lock")

        with lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            _RESEARCH_LOCK_CONTEXT.depth = 1
            try:
                yield
            finally:
                _RESEARCH_LOCK_CONTEXT.depth = 0
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _ensure_directory() -> None:
    RESEARCH_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )


def _load_raw_reports() -> list[dict]:
    _ensure_directory()

    if not RESEARCH_FILE.exists():
        return []

    with RESEARCH_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError(
            "Ventures research state must be a JSON list."
        )

    return data


def _save_raw_reports(
    reports: list[dict],
) -> None:
    _ensure_directory()

    temporary_file = RESEARCH_FILE.with_suffix(
        ".tmp"
    )

    with temporary_file.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            reports,
            file,
            indent=2,
            sort_keys=True,
        )

    temporary_file.replace(
        RESEARCH_FILE
    )


def save_research_report(
    report: VenturesResearchReport,
) -> dict:
    with research_write_lock():
        reports = _load_raw_reports()

        record = {
            "opportunity_id": report.opportunity_id,
            "created_at": _utc_now_iso(),
            "report": report.to_dict(),
        }
        reports.append(record)
        _save_raw_reports(reports)
        return record


def list_research_reports(
    opportunity_id: str | None = None,
) -> list[dict]:
    reports = _load_raw_reports()

    if opportunity_id is None:
        return reports

    return [
        record
        for record in reports
        if record["opportunity_id"]
        == opportunity_id
    ]


def get_latest_research_report(
    opportunity_id: str,
) -> dict | None:
    reports = list_research_reports(
        opportunity_id
    )

    if not reports:
        return None

    return reports[-1]
