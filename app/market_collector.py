import json
from datetime import datetime, timezone

from app.market_db.recorder import record_market_snapshot
from app.market_db.alerts import detect_and_store_alerts
from app.market_status import get_market_status


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def collect_market_snapshot() -> dict:
    snapshot = get_market_status(force_refresh=True)
    storage = record_market_snapshot(snapshot)
    alert_detection = detect_and_store_alerts(
        comparison_minutes=15,
        threshold_percent=0.75,
    )

    result = {
        "status": "success",
        "collector_finished_at": utc_now(),
        "market_checked_at": snapshot.get("checked_at"),
        "market_status": snapshot.get("status"),
        "market_summary": snapshot.get("summary"),
        "storage": storage,
        "alert_detection": alert_detection,
        "errors": snapshot.get("errors", []),
    }

    return result


if __name__ == "__main__":
    print(
        json.dumps(
            collect_market_snapshot(),
            indent=2,
        )
    )
