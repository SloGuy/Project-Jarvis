"""Autonomous paper creation and lifecycle decisions."""

import fcntl
import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.capital.autonomy_control import read_operating_policy
from app.capital.autonomy_paper_accounting import read_paper_accounting
from app.capital.autonomy_paper_creation import (
    create_autonomous_paper_experiment,
)
from app.capital.autonomy_paper_decisions import choose_paper_transition
from app.capital.autonomy_paper_lifecycle import transition_paper_experiment
from app.capital.experiment_factory_models import ExperimentFactoryRequest
from app.capital.experiment_factory_review import build_factory_review
from app.capital.experiment_factory_service import submit_factory_request
from app.capital.experiment_factory_store import ExperimentFactoryRecord
from app.capital.paper_lifecycle_store import PaperLifecycleRecord
from app.capital.research_store import locked_research_state


DIRECTORY = (
    Path(__file__).resolve().parents[2]
    / "runtime" / "capital" / "autonomy"
)


def manage_existing(request_key, policy):
    evidence = read_paper_accounting(request_key)
    eligible = False
    if evidence["status"] == "planned" and not policy.paused:
        review = build_factory_review(evidence["research_id"])
        eligible = (
            review["eligible_for_operator_review"] is True
            and not review["blockers"]
            and review["validation_gate"]["status"] == "passed"
            and bool(review["verified_plan_bindings"])
        )

    decision = choose_paper_transition(
        status=evidence["status"],
        accounting_valid=evidence["accounting_valid"],
        realized_gain_loss_usd=evidence["realized_gain_loss_usd"],
        sell_fill_count=evidence["sell_fill_count"],
        holding_count=evidence["holding_count"],
        active_age_seconds=evidence["active_age_seconds"],
        activation_eligible=eligible,
    )
    if decision["target"] is None:
        return {
            "request_key": request_key,
            "status": "unchanged",
            "decision": decision,
            "accounting_issues": evidence["issues"],
        }

    # Each state version has one decision identity. Concurrent changes
    # require fresh assessment; they must not be overwritten.
    receipt = transition_paper_experiment(
        request_key=request_key,
        target=decision["target"],
        decision_key=f"automatic:{evidence['version']}",
        expected_version=evidence["version"],
        reason=f"{decision['rule_version']}: {decision['reason']}",
    )
    return {
        "request_key": request_key,
        "status": "transitioned",
        "receipt": receipt,
        "evidence": evidence,
        "decision": decision,
    }


def create_next():
    with locked_research_state() as state:
        candidates = [
            dict(row) for row in state["candidates"].values()
            if row["status"] == "ready_for_experiment"
            and row["verdict"] == "promising"
            and row["strategy_name"] == "mean_reversion_v2"
            and row["asset_universe"] == ["BTC"]
        ]

    for candidate in sorted(candidates, key=lambda row: row["research_id"]):
        research_id = candidate["research_id"]
        with SessionLocal() as session:
            existing = session.scalar(
                select(ExperimentFactoryRecord).where(
                    ExperimentFactoryRecord.research_id == research_id
                )
            )
            if existing is not None:
                request_key = existing.request_key
                if existing.status != "awaiting_review":
                    continue
            else:
                request_key = None

        review = build_factory_review(research_id)
        if review["eligible_for_operator_review"] is not True:
            continue
        if request_key is None:
            identity = hashlib.sha256(research_id.encode()).hexdigest()[:32]
            request_key = f"capital:auto:{identity}"
            submit_factory_request(ExperimentFactoryRequest(
                request_key=request_key,
                research_id=research_id,
                requested_by="capital.lifecycle",
            ))
        return create_autonomous_paper_experiment(request_key)
    return {"status": "no_eligible_research"}


def run_lifecycle_cycle(*, directory=None):
    root = Path(directory) if directory is not None else DIRECTORY
    root.mkdir(parents=True, exist_ok=True)
    with (root / "lifecycle-worker.lock").open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy", "live_capital_authorized": False}

        policy = read_operating_policy()
        if not policy.enabled:
            return {"status": "disabled", "live_capital_authorized": False}

        with SessionLocal() as session:
            keys = list(session.scalars(
                select(PaperLifecycleRecord.request_key)
                .where(PaperLifecycleRecord.status != "retired")
                .order_by(PaperLifecycleRecord.request_key)
            ))

        outcomes = []
        failed = False
        for key in keys:
            try:
                outcomes.append(manage_existing(key, policy))
            except Exception as error:
                failed = True
                outcomes.append({
                    "request_key": key,
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "reason": str(error)[:2000],
                })

        creation = {"status": "deferred"}
        with SessionLocal() as session:
            occupied = session.scalar(
                select(PaperLifecycleRecord.request_key)
                .where(PaperLifecycleRecord.status != "retired")
                .limit(1)
            )
        if not policy.paused and not failed and occupied is None:
            creation = create_next()

        return {
            "status": "partial_failure" if failed else "completed",
            "outcomes": outcomes,
            "creation": creation,
            "live_capital_authorized": False,
        }


def main():
    try:
        result = run_lifecycle_cycle()
    except Exception as error:
        result = {
            "status": "failed",
            "error_type": type(error).__name__,
            "reason": str(error)[:2000],
            "live_capital_authorized": False,
        }
    print(json.dumps(result, indent=2))
    if result["status"] in {"failed", "partial_failure"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
