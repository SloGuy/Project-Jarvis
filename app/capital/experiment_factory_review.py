"""Read-only eligibility preview. Never authorizes experiment creation."""
from dataclasses import asdict
import json
from types import SimpleNamespace

from app.capital.committee_validation import build_validation_gate
from app.capital.research_service import require_research_candidate
from app.capital.research_models import ResearchStatus, ResearchVerdict
from app.capital.strategy_registry import require_strategy
from app.capital.validation_plan import research_snapshot
from app.capital.validation_registry import get_plan
from app.capital.run_validation import current_binding
from app.capital.run_evaluation import POLICY, encode
from app.capital.replay_manifest import capture_replay_manifest


def build_factory_review(research_id):
    candidate = require_research_candidate(research_id=research_id)
    strategy = require_strategy(strategy_name=candidate.strategy_name)
    blockers = []

    if (
        candidate.status != ResearchStatus.READY_FOR_EXPERIMENT
        or candidate.verdict != ResearchVerdict.PROMISING
    ):
        blockers.append("Research must be reviewed as ready and promising.")

    if not strategy.enabled:
        blockers.append("Strategy is disabled.")

    # The registered prospective runner currently supports MR2 only.
    if strategy.name != "mean_reversion_v2":
        blockers.append("Factory validation currently supports MR2 only.")

    experiment = SimpleNamespace(
        research_id=candidate.research_id,
        hypothesis_version=candidate.hypothesis_version,
        strategy_name=candidate.strategy_name,
        strategy_version=strategy.version,
    )
    gate = build_validation_gate(experiment)
    if gate.status.value != "passed":
        blockers.append("Registered validation gate has not passed.")

    bindings = []
    if not blockers:
        try:
            attempts = gate.actual_value["attempts"]
            if not attempts:
                raise ValueError("No verified attempts are available.")
            for attempt in attempts:
                row = get_plan(attempt["plan_id"])
                if row["status"] != "completed":
                    raise ValueError("Validation attempt is no longer completed.")
                plan = row["envelope"]["plan"]
                current_binding(plan)
                if plan["research"] != research_snapshot(candidate):
                    raise ValueError("Research changed during review.")
                bindings.append({
                    "plan_id": row["plan_id"],
                    "plan_sha256": row["registered_sha256"],
                })
        except (ValueError, RuntimeError, KeyError, OSError) as error:
            blockers.append(f"Current validation binding failed: {error}")

    eligible = not blockers
    return {
        "research": research_snapshot(candidate),
        "strategy": strategy.to_dict(),
        "eligible_for_operator_review": eligible,
        "blockers": blockers,
        "validation_gate": {
            "status": gate.status.value,
            "rationale": gate.rationale,
            "details": gate.actual_value,
        },
        "verified_plan_bindings": bindings if eligible else [],
        "proposed_experiment": {
            "status": "planned",
            "execution_mode": "disabled",
            "portfolio_type": "paper",
            "portfolio_active": False,
            "duration_days": 180,
            "starting_capital_usd": str(POLICY.starting_capital_usd),
            "policy": json.loads(json.dumps(asdict(POLICY), default=encode)),
            "execution_manifest": capture_replay_manifest(),
        } if eligible else None,
        "human_approval_required": True,
        "creation_authorized": False,
        "execution_authorized": False,
        "live_capital_authorized": False,
    }
