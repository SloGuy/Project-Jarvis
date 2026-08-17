import json
from datetime import datetime, timezone

from app.broad_market_attention import (
    get_attention_snapshot,
)
from app.broad_market_attention_processor import (
    process_active_attention,
)
from app.broad_market_scanner import (
    scan_broad_market,
)


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def run_broad_market_cycle() -> dict:
    scan = scan_broad_market()

    attention_processing = (
        process_active_attention()
    )

    attention_snapshot = (
        get_attention_snapshot()
    )

    return {
        "status": "success",
        "cycle_finished_at": utc_now(),
        "scan": scan,
        "attention_processing": (
            attention_processing
        ),
        "attention": attention_snapshot,
        "summary": {
            "broad_universe_size": (
                scan.get(
                    "total_broad_universe",
                    0,
                )
            ),
            "symbols_scanned": (
                scan.get(
                    "batch_size",
                    0,
                )
            ),
            "interesting_assets": (
                scan.get(
                    "interesting_count",
                    0,
                )
            ),
            "new_promotion_candidates": (
                scan.get(
                    "promotion_candidate_count",
                    0,
                )
            ),
            "active_attention_assets": (
                attention_snapshot.get(
                    "active_count",
                    0,
                )
            ),
            "attention_assets_processed": (
                attention_processing.get(
                    "processed_count",
                    0,
                )
            ),
            "attention_assets_continuing": (
                attention_processing.get(
                    "continuing_attention_count",
                    0,
                )
            ),
        },
    }


if __name__ == "__main__":
    print(
        json.dumps(
            run_broad_market_cycle(),
            indent=2,
        )
    )
