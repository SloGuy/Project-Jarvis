"""Select and advance autonomous validation work."""

import fcntl
import json

from app.capital import validation_registry as registry
from app.capital.autonomy_control import read_operating_policy
from app.capital.autonomy_research_review import review_validation_outcome
from app.capital.autonomy_validation_draft import (
    prepare_validation_draft,
    register_saved_validation,
)
from app.capital.autonomy_validation_evaluation import process_validation
from app.capital.autonomy_validation_registration import PREFIX
from app.capital.research_models import ResearchCandidate, ResearchStatus
from app.capital.research_store import locked_research_state
from app.capital.validation_plan import timestamp


ELIGIBLE_STATUSES = {
    ResearchStatus.PROPOSED,
    ResearchStatus.SCREENING,
    ResearchStatus.RESEARCHING,
    ResearchStatus.READY_FOR_EXPERIMENT,
}


def _recorded(candidate, row):
    """Selection hint only; promotion must independently verify evidence."""
    if candidate is None:
        return False

    def matches(records):
        return any(
            item.get("plan_id") == row["plan_id"]
            and item.get("plan_sha256") == row["registered_sha256"]
            for item in records
        )

    return (
        matches(candidate.validation_assessments)
        and matches(candidate.validation_recommendations)
    )


def _cycle():
    policy = read_operating_policy()
    if not policy.enabled:
        return {"status": "disabled"}
    if policy.paused:
        return {"status": "paused"}

    with registry.locked_state() as state:
        plans = registry.copy_value(list(state["plans"].values()))

    with locked_research_state() as state:
        candidates = {
            key: ResearchCandidate.from_dict(row)
            for key, row in state["candidates"].items()
        }

    with locked_research_state() as state:
        review_requests = state.get("review_requests", {})
        if not isinstance(review_requests, dict):
            raise RuntimeError("Invalid research review request store.")
        reviewed = set(review_requests)

    owned = [
        row for row in plans
        if row["envelope"]["plan"]["created_by"].startswith(PREFIX)
    ]
    now = registry.now_utc()

    # Complete one actionable plan before starting new work.
    for row in sorted(owned, key=lambda item: item["plan_id"]):
        plan = row["envelope"]["plan"]
        candidate = candidates.get(plan["research"]["research_id"])

        collection_key = (
            "provider_collection"
            if plan["schema_version"] == 2
            else "witness_collection"
        )
        needs_recording = (
            row["status"] == "completed" and not _recorded(candidate, row)
        )
        ready_to_execute = (
            row["status"] == "registered"
            and now >= timestamp(plan["end_exclusive"])
            and row.get(collection_key, {}).get("status") == "sealed"
        )
        if needs_recording or ready_to_execute:
            return process_validation(row["plan_id"])

    for row in sorted(owned, key=lambda item: item["plan_id"]):
        research_id = row["envelope"]["plan"]["research"]["research_id"]
        if (
            row["status"] == "completed"
            and _recorded(candidates.get(research_id), row)
            and f"capital-review:{row['plan_id']}" not in reviewed
        ):
            return review_validation_outcome(row["plan_id"])

    # One active automated validation at a time. Collection has its own
    # schedule and continues while this worker waits.
    active = [
        row for row in owned
        if row["status"] in {"registered", "running"}
    ]
    if active:
        return {
            "status": "validation_in_progress",
            "plans": [
                {"plan_id": row["plan_id"], "status": row["status"]}
                for row in active
            ],
        }

    # Any registered attempt, including a failed one, consumes this
    # candidate/version's automatic attempt. A reviewed revision is
    # required before scheduling a new hypothesis version.
    attempted = {
        (
            row["envelope"]["plan"]["research"]["research_id"],
            row["envelope"]["plan"]["research"]["hypothesis_version"],
        )
        for row in plans
    }

    for candidate in sorted(
        candidates.values(),
        key=lambda item: (item.created_at, item.research_id),
    ):
        if (
            candidate.status not in ELIGIBLE_STATUSES
            or candidate.strategy_name != "mean_reversion_v2"
            or [item.strip().upper() for item in candidate.asset_universe]
            != ["BTC"]
            or (candidate.research_id, candidate.hypothesis_version)
            in attempted
        ):
            continue

        # Stable work identity, also used by the saved-draft store.
        work_id = (
            f"validation:{candidate.research_id}:"
            f"{candidate.hypothesis_version}"
        )
        prepare_validation_draft(
            task_id=work_id,
            research_id=candidate.research_id,
        )
        registered = register_saved_validation(task_id=work_id)
        return {
            "status": "registered",
            "plan_id": registered["plan_id"],
            "research_id": candidate.research_id,
        }

    return {"status": "no_new_validation_work"}


def run_validation_cycle():
    registry.DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = registry.DIRECTORY / "autonomy-validation-worker.lock"
    with path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy", "live_capital_authorized": False}

        try:
            result = _cycle()
            return {**result, "live_capital_authorized": False}
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    print(json.dumps(run_validation_cycle(), indent=2), flush=True)
