from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from datetime import datetime, timezone

from app.ventures import assessment_attempt_store as attempts
from app.ventures import assessment_store
from app.ventures.assessment_runner import (
    assess_opportunity,
    assessment_configuration,
)
from app.ventures.discovery_screening_store import list_research_queue
from app.ventures.discovery_store import list_discovery_runs
from app.ventures.opportunities import get_opportunity
from app.ventures.research_store import get_latest_research_report


MAX_SCAN_AGE_SECONDS = 12 * 60 * 60
RETRY_DELAY_SECONDS = 6 * 60 * 60


@contextmanager
def _worker_lock():
    attempts.ATTEMPT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = attempts.ATTEMPT_DIRECTORY / "worker.lock"
    with path.open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "Another automatic assessment worker is running."
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _age_seconds(timestamp: str) -> float:
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone.")
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _latest_scan() -> dict:
    runs = [
        run for run in list_discovery_runs()
        if run["source"] == "empire_flippers"
    ]
    if not runs or runs[-1]["status"] != "success":
        raise ValueError("No current successful discovery run.")
    run = runs[-1]
    age = _age_seconds(run["snapshot"]["finished_at"])
    if age < 0 or age > MAX_SCAN_AGE_SECONDS:
        raise ValueError("Discovery snapshot is stale or future-dated.")
    return run


def _eligible_research(entry: dict, run: dict):
    if (
        entry["status"] != "collected"
        or entry["discovery_run_id"] != run["run_id"]
        or entry.get("collected_evaluation_fingerprint")
        != entry["evaluation_fingerprint"]
    ):
        return None

    opportunity = get_opportunity(entry["opportunity_id"])
    if opportunity is None or opportunity.status.value not in {
        "discovered", "screening", "research"
    }:
        return None

    source_ids = {
        item["source_id"]
        for item in run["intake_results"]
        if item["outcome"] == "linked"
        and item["opportunity_id"] == opportunity.opportunity_id
    }
    snapshot_ids = {
        listing["id"] for listing in run["snapshot"]["listings"]
    }
    if (
        len(source_ids) != 1
        or not source_ids <= snapshot_ids
        or opportunity.source
        != f"empire_flippers:{next(iter(source_ids))}"
    ):
        return None

    return get_latest_research_report(opportunity.opportunity_id)


def run_assessment_worker() -> dict:
    with _worker_lock():
        run = _latest_scan()
        configuration = assessment_configuration()
        history = attempts.list_attempts()
        cached_count = 0
        delayed_count = 0

        for entry in list_research_queue():
            research = _eligible_research(entry, run)
            if research is None:
                continue

            key = assessment_store.assessment_key(
                research_record=research,
                configuration=configuration,
            )
            if assessment_store.get_assessment(key) is not None:
                cached_count += 1
                continue

            previous = next(
                (
                    item for item in reversed(history)
                    if item["assessment_key"] == key
                ),
                None,
            )
            if previous is not None:
                age = _age_seconds(
                    previous["finished_at"] or previous["started_at"]
                )
                if age < 0 or (
                    previous["status"] in {"running", "failed", "deferred"}
                    and age < RETRY_DELAY_SECONDS
                ):
                    delayed_count += 1
                    continue

            attempt = attempts.start_attempt(
                opportunity_id=entry["opportunity_id"],
                assessment_key=key,
            )

            try:
                # Recheck eligibility after selection, before generation.
                latest_run = _latest_scan()
                current_entry = next(
                    (
                        item for item in list_research_queue()
                        if item["opportunity_id"] == entry["opportunity_id"]
                    ),
                    None,
                )
                current_research = (
                    _eligible_research(current_entry, latest_run)
                    if current_entry is not None else None
                )
                if (
                    current_research != research
                    or assessment_configuration() != configuration
                ):
                    attempts.finish_attempt(
                        attempt,
                        status="deferred",
                        error="Inputs or queue eligibility changed.",
                    )
                    return {
                        "status": "deferred",
                        "opportunity_id": entry["opportunity_id"],
                    }

                result = assess_opportunity(
                    entry["opportunity_id"],
                    expected_assessment_key=key,
                )
                if result["record"]["assessment_key"] != key:
                    raise ValueError(
                        "Runner used a newer input version; "
                        "inspect saved assessment history."
                    )

                attempts.finish_attempt(attempt, status=result["status"])
                return {
                    "status": "success",
                    "outcome": result["status"],
                    "opportunity_id": entry["opportunity_id"],
                    "assessment_key": key,
                    "cached_skipped": cached_count,
                    "retry_delayed": delayed_count,
                }
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                attempts.finish_attempt(attempt, status="failed", error=error)
                return {
                    "status": "failed",
                    "opportunity_id": entry["opportunity_id"],
                    "error": error,
                }

        return {
            "status": "success",
            "outcome": "no_candidate_due",
            "cached_skipped": cached_count,
            "retry_delayed": delayed_count,
        }


def main():
    try:
        result = run_assessment_worker()
    except Exception as exc:
        result = {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
    print(json.dumps(result, indent=2))
    if result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
