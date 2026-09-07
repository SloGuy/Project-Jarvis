from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from app.ventures.discovery_screening_store import (
    list_research_queue,
    record_research_collection,
)
from app.ventures.discovery_store import (
    discovery_run_lock,
    list_discovery_runs,
)
from app.ventures.opportunities import get_opportunity
from app.ventures.research_source_claims import (
    EXTRACTOR_VERSION,
    extract_source_claims,
)
from app.ventures.research_source_merge import merge_source_research


MAX_SNAPSHOT_AGE_SECONDS = 12 * 60 * 60


def _source_fingerprint(claims) -> str:
    payload = {
        "extractor": EXTRACTOR_VERSION,
        "claim_ids": sorted(claim.claim_id for claim in claims),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()


def collect_queued_research() -> dict:
    with discovery_run_lock():
        runs = [
            run for run in list_discovery_runs()
            if run["source"] == "empire_flippers"
        ]
        if not runs:
            raise ValueError("No discovery run is available.")

        run = runs[-1]
        if run["status"] != "success":
            raise ValueError("Latest discovery run failed; research deferred.")

        snapshot = run.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError("Latest discovery snapshot is missing.")

        fetched_at = snapshot["finished_at"]
        collected_time = datetime.fromisoformat(fetched_at)
        if collected_time.tzinfo is None:
            raise ValueError("Snapshot timestamp must include a timezone.")

        age = (
            datetime.now(timezone.utc) - collected_time
        ).total_seconds()
        if age < 0 or age > MAX_SNAPSHOT_AGE_SECONDS:
            raise ValueError("Discovery snapshot is stale or future-dated.")

        listings = {
            listing["id"]: listing for listing in snapshot["listings"]
        }
        if len(listings) != len(snapshot["listings"]):
            raise ValueError("Duplicate source IDs in discovery snapshot.")

        linked = {
            result["opportunity_id"]: result["source_id"]
            for result in run["intake_results"]
            if result["outcome"] == "linked"
        }

        results = []
        for entry in list_research_queue():
            if entry["status"] == "inactive":
                continue

            opportunity_id = entry["opportunity_id"]
            fingerprint = None
            try:
                if entry["discovery_run_id"] != run["run_id"]:
                    raise ValueError("Queue entry belongs to an older scan.")

                opportunity = get_opportunity(opportunity_id)
                if opportunity is None:
                    raise ValueError("Opportunity no longer exists.")
                if opportunity.status.value not in {
                    "discovered", "screening", "research"
                }:
                    results.append({
                        "opportunity_id": opportunity_id,
                        "outcome": "lifecycle_skipped",
                    })
                    continue

                source_id = linked.get(opportunity_id)
                if source_id is None or source_id not in listings:
                    raise ValueError("Opportunity is absent from latest scan.")
                if opportunity.source != f"empire_flippers:{source_id}":
                    raise ValueError("Opportunity source mismatch.")

                listing = listings[source_id]
                claims = extract_source_claims(
                    listing=listing,
                    source_url=opportunity.source_url,
                    fetched_at=fetched_at,
                )
                fingerprint = _source_fingerprint(claims)

                if (
                    entry["status"] == "collected"
                    and entry.get("collected_source_fingerprint")
                    == fingerprint
                    and entry.get("collected_evaluation_fingerprint")
                    == entry["evaluation_fingerprint"]
                ):
                    results.append({
                        "opportunity_id": opportunity_id,
                        "outcome": "unchanged",
                    })
                    continue

                merged = merge_source_research(
                    opportunity=opportunity,
                    listing=listing,
                    source_url=opportunity.source_url,
                    fetched_at=fetched_at,
                )
                recorded = record_research_collection(
                    opportunity_id=opportunity_id,
                    expected_discovery_run_id=entry["discovery_run_id"],
                    expected_evaluation_fingerprint=(
                        entry["evaluation_fingerprint"]
                    ),
                    source_fingerprint=fingerprint,
                    report_created_at=merged["record"]["created_at"],
                )
                if not recorded:
                    raise ValueError(
                        "Queue changed during collection; completion deferred."
                    )

                results.append({
                    "opportunity_id": opportunity_id,
                    "outcome": "collected",
                    "added_claims": merged["added_claims"],
                    "report_changed": merged["changed"],
                })
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                if fingerprint is not None:
                    record_research_collection(
                        opportunity_id=opportunity_id,
                        expected_discovery_run_id=entry["discovery_run_id"],
                        expected_evaluation_fingerprint=(
                            entry["evaluation_fingerprint"]
                        ),
                        source_fingerprint=fingerprint,
                        error=error,
                    )
                results.append({
                    "opportunity_id": opportunity_id,
                    "outcome": "failed",
                    "error": error,
                })

        failed = sum(item["outcome"] == "failed" for item in results)
        return {
            "status": "failed" if failed else "success",
            "discovery_run_id": run["run_id"],
            "collection_scope": "public_listing_snapshot",
            "collected_count": sum(
                item["outcome"] == "collected" for item in results
            ),
            "unchanged_count": sum(
                item["outcome"] == "unchanged" for item in results
            ),
            "failed_count": failed,
            "results": results,
            "independent_verification_performed": False,
            "automatic_purchase_authority": False,
        }


def main():
    try:
        result = collect_queued_research()
    except Exception as exc:
        result = {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }

    print(json.dumps(result, indent=2))
    if result["status"] != "success":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
