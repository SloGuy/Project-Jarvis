"""Evaluate one automation-owned plan and record verified outcomes."""

import fcntl

from app.agents.capital_registry import VALIDATION_AGENT_ID
from app.capital.autonomy_control import read_operating_policy
from app.capital.autonomy_policy import authorize_capital_action
from app.capital.autonomy_validation_registration import PREFIX
from app.capital import validation_registry as registry
from app.capital.validation_collection import (
    store_for,
    validate_collection,
)
from app.capital.validation_plan import timestamp
from app.capital.run_validation import execute_registered
from app.capital.validation_research import (
    attach_completed,
    record_recommendation,
)


def _authorize(action):
    authorize_capital_action(
        agent_id=VALIDATION_AGENT_ID,
        action=action,
        policy=read_operating_policy(),
        execution_mode="paper",
    )


def _process(plan_id):
    _authorize("capital.inspect_evidence")
    row = registry.get_plan(plan_id)
    plan = row["envelope"]["plan"]

    if not plan["created_by"].startswith(PREFIX):
        raise ValueError("Plan is not owned by Capital automation.")

    if row["status"] in {"running", "failed"}:
        return {
            "plan_id": plan_id,
            "status": "blocked",
            "reason": (
                "Existing running or failed validation requires recovery "
                "review; it must not be automatically rerun."
            ),
            "run_status": row["status"],
        }

    if row["status"] == "registered":
        if registry.now_utc() < timestamp(plan["end_exclusive"]):
            return {"plan_id": plan_id, "status": "waiting_for_deadline"}

        witness = row.get("witness_collection")
        if witness is None:
            raise ValueError("Validation has no bound collection.")

        validate_collection(witness, row["registered_sha256"])
        if witness["status"] != "sealed":
            return {"plan_id": plan_id, "status": "waiting_for_seal"}
        if store_for(witness).read_checkpoint() != witness["store_checkpoint"]:
            raise ValueError("Sealed collection checkpoint differs.")

        _authorize("capital.run_validation")
        execute_registered(plan_id)
        row = registry.get_plan(plan_id)

    if row["status"] != "completed":
        raise ValueError("Validation did not reach completed status.")

    # These operations verify the saved packet independently and tolerate
    # an earlier successful attachment followed by interruption.
    _authorize("capital.assess_validation")
    attached = attach_completed(plan_id)
    _authorize("capital.assess_validation")
    recommendation = record_recommendation(plan_id)

    return {
        "plan_id": plan_id,
        "status": "outcome_recorded",
        "research_id": attached["research"]["research_id"],
        "validation_status": attached["assessment"]["validation_status"],
        "recommendation": recommendation["recommendation"],
        "promotion_authorized": False,
        "live_capital_authorized": False,
    }


def process_validation(plan_id):
    """Serialize evaluation independently of collection and model work.

    An interrupted claimed run is never reset here. Subsequent attempts
    can resume outcome attachment only after the registry says completed.
    """
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise ValueError("A validation plan ID is required.")

    registry.DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = registry.DIRECTORY / "autonomy-evaluation.lock"

    with path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"plan_id": plan_id, "status": "busy"}

        try:
            return _process(plan_id)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
