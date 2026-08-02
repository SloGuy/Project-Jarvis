from datetime import datetime, timezone
from pathlib import Path
import subprocess


BACKUP_DIR = Path(
    "/home/sloguy/jarvis-core/backups/postgres"
)

BACKUP_PATTERN = "jarvis_market_*.dump"
STALE_AFTER_HOURS = 30


def _timer_active() -> bool:
    result = subprocess.run(
        [
            "/usr/bin/systemctl",
            "is-active",
            "jarvis-market-backup.timer",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )

    return result.stdout.strip() == "active"


def get_market_backup_status() -> dict:
    backups = sorted(
        BACKUP_DIR.glob(BACKUP_PATTERN),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    latest_backup = backups[0] if backups else None

    if latest_backup is None:
        return {
            "status": "unavailable",
            "summary": "No market database backups were found.",
            "backup_count": 0,
            "latest_backup": None,
            "timer_active": _timer_active(),
        }

    latest_modified = datetime.fromtimestamp(
        latest_backup.stat().st_mtime,
        tz=timezone.utc,
    )

    age_seconds = (
        datetime.now(timezone.utc) - latest_modified
    ).total_seconds()

    stale = age_seconds > STALE_AFTER_HOURS * 3600
    timer_active = _timer_active()

    if stale or not timer_active:
        status = "warning"
    else:
        status = "healthy"

    summary = (
        f"{len(backups)} market database backups retained. "
        f"Latest backup is {age_seconds / 3600:.2f} hours old."
    )

    return {
        "status": status,
        "summary": summary,
        "backup_count": len(backups),
        "timer_active": timer_active,
        "stale_after_hours": STALE_AFTER_HOURS,
        "latest_backup": {
            "filename": latest_backup.name,
            "size_bytes": latest_backup.stat().st_size,
            "created_at": latest_modified.isoformat(),
            "age_seconds": round(age_seconds, 1),
            "stale": stale,
        },
    }
