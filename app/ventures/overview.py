from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from app.ventures.assessment_runner import assessment_configuration
from app.ventures.assessment_store import assessment_key, list_assessments
from app.ventures.discovery_screening_store import (
    list_discovery_evaluations,
    list_research_queue,
)
from app.ventures.discovery_store import list_discovery_runs
from app.ventures.opportunities import list_opportunities
from app.ventures.research_store import list_research_reports
from app.ventures.review_priority import review_priority


def build_ventures_overview() -> dict:
    now = datetime.now(timezone.utc)
    opportunities = list_opportunities()
    queue = {
        item["opportunity_id"]: item for item in list_research_queue()
    }

    evaluations = {}
    for record in list_discovery_evaluations():
        evaluations[record["opportunity_id"]] = record

    research = {}
    for record in list_research_reports():
        research[record["opportunity_id"]] = record

    assessments = {}
    for record in list_assessments():
        assessments.setdefault(record["opportunity_id"], []).append(record)

    runs = [
        run for run in list_discovery_runs()
        if run["source"] == "empire_flippers"
    ]
    latest_run = runs[-1] if runs else None
    scan_fresh = False
    active_sources = {}
    scan_note = None

    if latest_run and latest_run["status"] == "success":
        snapshot = latest_run.get("snapshot")
        if isinstance(snapshot, dict):
            try:
                timestamp = datetime.fromisoformat(snapshot["finished_at"])
                if timestamp.tzinfo is None:
                    raise ValueError("Missing timezone.")
                age = (now - timestamp).total_seconds()
                scan_fresh = 0 <= age <= 12 * 60 * 60

                source_ids = {
                    item["id"] for item in snapshot["listings"]
                }
                active_sources = {
                    item["opportunity_id"]: item["source_id"]
                    for item in latest_run["intake_results"]
                    if item["outcome"] == "linked"
                    and item["source_id"] in source_ids
                }
            except (ValueError, TypeError, KeyError):
                scan_fresh = False
                scan_note = "Discovery freshness could not be established."
        else:
            scan_note = "Discovery snapshot is missing."

    configuration = assessment_configuration()
    rows = []

    for opportunity in opportunities:
        opportunity_id = opportunity.opportunity_id
        report = research.get(opportunity_id)
        evaluation_record = evaluations.get(opportunity_id)
        evaluation = (
            evaluation_record["evaluation"] if evaluation_record else None
        )
        entry = queue.get(opportunity_id)
        drafts = assessments.get(opportunity_id, [])

        current_key = (
            assessment_key(
                research_record=report,
                configuration=configuration,
            )
            if report is not None else None
        )
        current = next(
            (
                draft for draft in reversed(drafts)
                if draft["assessment_key"] == current_key
            ),
            None,
        )
        assessment_status = (
            "current_draft" if current is not None
            else "stale_draft" if drafts
            else "not_assessed"
        )

        source_id = active_sources.get(opportunity_id)
        source_current = bool(
            scan_fresh
            and source_id is not None
            and opportunity.source == f"empire_flippers:{source_id}"
        )
        queue_current = bool(
            source_current
            and entry is not None
            and entry["discovery_run_id"] == latest_run["run_id"]
        )
        eligible_stage = opportunity.status.value in {
            "discovered", "screening", "research"
        }
        candidate = bool(
            queue_current
            and eligible_stage
            and entry["status"] != "inactive"
            and evaluation is not None
            and evaluation["research_candidate"]
        )

        missing_inputs = [
            field
            for field in (
                "annual_revenue_usd",
                "annual_sde_usd",
                "owner_hours_per_week",
            )
            if getattr(opportunity, field) is None
        ]
        priority = review_priority(
            candidate=candidate,
            research=report,
            assessment_status=assessment_status,
            today=now.date(),
        )

        rows.append({
            "opportunity_id": opportunity_id,
            "name": opportunity.name,
            "business_type": opportunity.business_type.value,
            "status": opportunity.status.value,
            "asking_price_usd": opportunity.asking_price_usd,
            "source_url": opportunity.source_url,
            "source_current": source_current,
            "research_candidate": candidate,
            "queue_status": entry["status"] if entry else None,
            "disposition": (
                evaluation["disposition"] if evaluation else "not_screened"
            ),
            "missing_inputs": missing_inputs,
            "research_created_at": (
                report["created_at"] if report else None
            ),
            "assessment_status": assessment_status,
            "thesis_fit": (
                current["result"]["assessment"]["thesis_fit"]
                if current else None
            ),
            "assessment_key": (
                current["assessment_key"] if current else None
            ),
            "review_priority": priority,
        })

    rows.sort(key=lambda row: (
        row["review_priority"]["sort_order"],
        row["assessment_status"] != "current_draft",
        row["name"].lower(),
        row["opportunity_id"],
    ))

    return {
        "generated_at": now.isoformat(),
        "count": len(rows),
        "research_candidate_count": sum(
            row["research_candidate"] for row in rows
        ),
        "assessment_counts": dict(Counter(
            row["assessment_status"] for row in rows
        )),
        "priority_counts": dict(Counter(
            row["review_priority"]["group"] for row in rows
        )),
        "latest_discovery": (
            {
                "run_id": latest_run["run_id"],
                "status": latest_run["status"],
                "finished_at": latest_run["finished_at"],
                "error": latest_run.get("error") or scan_note,
                "snapshot_fresh": scan_fresh,
            }
            if latest_run else None
        ),
        "ordering": "review_priority_v1",
        "opportunities": rows,
        "automatic_purchase_authority": False,
        "capital_transfer_authority": False,
    }
