"""Automatic research review from verified validation outcomes."""

import fcntl
import json

from app.agents.capital_registry import LIFECYCLE_AGENT_ID
from app.capital.autonomy_control import read_operating_policy
from app.capital.autonomy_policy import POLICY_VERSION, authorize_capital_action
from app.capital.autonomy_validation_registration import PREFIX
from app.capital import validation_registry as registry
from app.capital.validation_research import inspect_completed
from app.capital.validation_recommendation import build_validation_recommendation
from app.capital.research_models import ResearchStatus, ResearchVerdict
from app.capital.research_service import require_research_candidate
from app.capital.research_store import locked_research_state
from app.capital.research_workflow import (
    begin_research_screening,
    begin_strategy_research,
    evaluate_research_candidate,
)
from app.capital.validation_plan import research_snapshot


def _authorize():
    authorize_capital_action(
        agent_id=LIFECYCLE_AGENT_ID,
        action="capital.review_research",
        policy=read_operating_policy(),
        execution_mode="paper",
    )


def _review(plan_id):
    _authorize()
    row = registry.get_plan(plan_id)
    if not row["envelope"]["plan"]["created_by"].startswith(PREFIX):
        raise ValueError("Plan is not owned by Capital automation.")

    verified = inspect_completed(plan_id)
    assessment = verified["assessment"]
    advisory = build_validation_recommendation(assessment)
    verdict = ResearchVerdict(advisory["research_verdict"])
    research_id = verified["research"]["research_id"]
    request_key = f"capital-review:{plan_id}"

    evidence = [
        f"Registered validation: {plan_id}",
        f"Plan SHA256: {verified['plan_sha256']}",
        f"Report SHA256: {verified['report_sha256']}",
        f"Assessment SHA256: {verified['assessment_sha256']}",
    ]
    concerns = [
        *assessment.get("insufficient_evidence_reasons", []),
        *[
            f"Failed performance criterion: {name}"
            for name in assessment.get("failed_performance_criteria", [])
        ],
        *assessment.get("limitations", []),
    ]
    notes = json.dumps({
        "actor": LIFECYCLE_AGENT_ID,
        "policy_version": POLICY_VERSION,
        "decision_basis": "verified_registered_validation",
        "plan_id": plan_id,
        "validation_status": advisory["validation_status"],
        "advisory_recommendation": advisory["recommendation"],
        "scope": "research_review_only",
        "promotion_authorized": False,
        "live_capital_authorized": False,
    }, sort_keys=True)

    with locked_research_state() as state:
        requests = state.get("review_requests", {})
        if not isinstance(requests, dict):
            raise RuntimeError("Invalid research review request store.")
        already_reviewed = request_key in requests

    if not already_reviewed:
        candidate = require_research_candidate(research_id=research_id)
        if research_snapshot(candidate) != verified["research"]:
            raise ValueError("Research differs from verified validation.")

        if candidate.status == ResearchStatus.PROPOSED:
            _authorize()
            candidate = begin_research_screening(research_id=research_id)

        if candidate.status == ResearchStatus.SCREENING:
            _authorize()
            candidate = begin_strategy_research(research_id=research_id)

        if candidate.status != ResearchStatus.RESEARCHING:
            raise ValueError(
                "Research already has another review or is not reviewable."
            )

    _authorize()
    candidate = evaluate_research_candidate(
        research_id=research_id,
        verdict=verdict,
        evidence=evidence,
        concerns=concerns,
        evaluation_notes=notes,
        expected_research_snapshot=verified["research"],
        request_key=request_key,
    )

    return {
        "status": "research_review_recorded",
        "plan_id": plan_id,
        "research_id": candidate.research_id,
        "research_status": candidate.status.value,
        "research_verdict": candidate.verdict.value,
        "policy_version": POLICY_VERSION,
        "promotion_authorized": False,
        "live_capital_authorized": False,
    }


def review_validation_outcome(plan_id):
    """Serialize reviews; persisted request receipts make retries safe."""
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise ValueError("A validation plan ID is required.")

    registry.DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = registry.DIRECTORY / "autonomy-research-review.lock"
    with path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return _review(plan_id)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
