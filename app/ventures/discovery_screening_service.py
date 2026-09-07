from __future__ import annotations

from app.ventures.discovery_screening import (
    evaluate_discovered_opportunity,
)
from app.ventures.discovery_screening_store import (
    reconcile_research_queue,
    save_evaluation,
)
from app.ventures.opportunities import get_opportunity


def screen_discovery_run(run: dict) -> dict:
    if run["status"] != "success":
        raise ValueError("Only successful discovery runs may be screened.")

    snapshot = run.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("Discovery snapshot is required.")

    if run["source"] != "empire_flippers":
        raise ValueError("Unsupported discovery source.")

    source_ids = {
        listing["id"] for listing in snapshot["listings"]
    }
    targets = {}
    for result in run["intake_results"]:
        if result["outcome"] != "linked":
            continue

        source_id = result["source_id"]
        if source_id not in source_ids:
            raise ValueError("Linked listing is absent from the snapshot.")

        opportunity_id = result["opportunity_id"]
        if (
            opportunity_id in targets
            and targets[opportunity_id] != source_id
        ):
            raise ValueError("Conflicting opportunity source references.")
        targets[opportunity_id] = source_id

    # Validate every target before beginning screening writes.
    prepared = []
    for opportunity_id, source_id in targets.items():
        opportunity = get_opportunity(opportunity_id)
        if opportunity is None:
            raise ValueError("Linked opportunity no longer exists.")
        if opportunity.source != f"empire_flippers:{source_id}":
            raise ValueError("Opportunity source does not match discovery.")

        evaluation = evaluate_discovered_opportunity(opportunity)
        prepared.append((opportunity.to_dict(), evaluation))

    results = []
    for opportunity_snapshot, evaluation in prepared:
        results.append(save_evaluation(
            opportunity_snapshot=opportunity_snapshot,
            evaluation=evaluation,
            discovery_run_id=run["run_id"],
        ))

    deactivated = reconcile_research_queue(
        source=run["source"],
        active_opportunity_ids=set(targets),
        discovery_run_id=run["run_id"],
    )

    dispositions = {}
    for result in results:
        key = result["disposition"]
        dispositions[key] = dispositions.get(key, 0) + 1

    return {
        "status": "success",
        "evaluated_count": len(results),
        "changed_count": sum(
            result["evaluation_changed"] for result in results
        ),
        "pending_count": sum(
            result["queue_status"] == "pending" for result in results
        ),
        "dispositions": dispositions,
        "deactivated_count": deactivated,
    }
