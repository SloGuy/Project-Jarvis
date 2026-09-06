from __future__ import annotations

import json
from datetime import datetime, timezone

from app.ventures.discovery_empire_flippers import fetch_all_listings
from app.ventures.discovery_intake import import_listing
from app.ventures.discovery_store import (
    discovery_run_lock,
    save_discovery_run,
)


SOURCE = "empire_flippers"


def collect_discovery() -> dict:
    started_at = datetime.now(timezone.utc).isoformat()

    with discovery_run_lock():
        snapshot = None
        intake_results = []
        error = None

        try:
            snapshot = fetch_all_listings()

            for listing in snapshot["listings"]:
                intake_results.append(import_listing(listing))

        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        status = "failed" if error is not None else "success"

        record = save_discovery_run(
            status=status,
            started_at=started_at,
            source=SOURCE,
            snapshot=snapshot,
            intake_results=intake_results,
            error=error,
        )

        linked = sum(
            item["outcome"] == "linked"
            for item in intake_results
        )
        skipped = sum(
            item["outcome"] == "skipped"
            for item in intake_results
        )

        return {
            "status": status,
            "run_id": record["run_id"],
            "source": SOURCE,
            "started_at": started_at,
            "finished_at": record["finished_at"],
            "total_reported": (
                snapshot["total_reported"]
                if snapshot is not None else None
            ),
            "fetched_count": (
                snapshot["fetched_count"]
                if snapshot is not None else None
            ),
            "processed_count": len(intake_results),
            "linked_count": linked,
            "skipped_count": skipped,
            "error": error,
            "automatic_offer_authority": False,
            "automatic_purchase_authority": False,
            "capital_transfer_authority": False,
        }


def main():
    try:
        result = collect_discovery()
    except Exception as exc:
        # Lock and storage failures must also produce a nonzero exit.
        print(json.dumps({
            "status": "failed",
            "source": SOURCE,
            "error": f"{type(exc).__name__}: {exc}",
            "run_record_saved": False,
        }, indent=2))
        raise SystemExit(1)

    print(json.dumps(result, indent=2))
    if result["status"] != "success":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
