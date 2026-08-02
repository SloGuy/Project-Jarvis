from datetime import datetime, timezone
import subprocess

from app.market_db.backup_status import get_market_backup_status
from app.market_db.intelligence import _database_statistics


COLLECTOR_TIMER = "jarvis-market-collector.timer"
FRESH_AFTER_SECONDS = 1800


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _unit_active(unit_name: str) -> bool:
    try:
        result = subprocess.run(
            [
                "/usr/bin/systemctl",
                "is-active",
                unit_name,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )

        return result.stdout.strip() == "active"

    except (OSError, subprocess.SubprocessError):
        return False



def _collector_service_result() -> dict:
    try:
        result = subprocess.run(
            [
                "/usr/bin/systemctl",
                "show",
                "jarvis-market-collector.service",
                "--property=Result",
                "--property=ExecMainCode",
                "--property=ExecMainStatus",
                "--property=InactiveEnterTimestamp",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )

        values = {}

        for line in result.stdout.splitlines():
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            values[key] = value

        return {
            "result": values.get("Result") or "unknown",
            "exec_main_code": values.get("ExecMainCode"),
            "exec_main_status": values.get("ExecMainStatus"),
            "last_finished_at": (
                values.get("InactiveEnterTimestamp")
                or None
            ),
        }

    except (OSError, subprocess.SubprocessError):
        return {
            "result": "unknown",
            "exec_main_code": None,
            "exec_main_status": None,
            "last_finished_at": None,
        }



def _collector_health(database: dict) -> dict:
    timer_active = _unit_active(COLLECTOR_TIMER)
    service_result = _collector_service_result()

    latest_age = database.get(
        "latest_observation_age_seconds"
    )

    fresh = (
        latest_age is not None
        and latest_age <= FRESH_AFTER_SECONDS
    )

    if timer_active and fresh:
        status = "healthy"
        summary = (
            "Market collector timer is active and "
            "stored market data is fresh."
        )
    elif timer_active:
        status = "warning"
        summary = (
            "Market collector timer is active, "
            "but stored market data may be stale."
        )
    else:
        status = "critical"
        summary = (
            "Market collector timer is not active."
        )

    return {
        "status": status,
        "summary": summary,
        "timer_active": timer_active,
        "timer_unit": COLLECTOR_TIMER,
        "fresh": fresh,
        "fresh_after_seconds": FRESH_AFTER_SECONDS,
        "last_collection_at": database.get(
            "latest_observation_at"
        ),
        "last_collection_age_seconds": latest_age,
        "last_run_result": service_result["result"],
        "last_run_exit_status": service_result[
            "exec_main_status"
        ],
        "last_run_finished_at": service_result[
            "last_finished_at"
        ],
}


def _database_health() -> dict:
    try:
        statistics = _database_statistics()

    except Exception as error:
        return {
            "status": "critical",
            "summary": (
                "Market database could not be queried."
            ),
            "reachable": False,
            "error": str(error),
            "statistics": None,
        }

    latest_age = statistics.get(
        "latest_observation_age_seconds"
    )

    if latest_age is None:
        status = "warning"
        summary = (
            "Market database is reachable, but no "
            "observations have been stored."
        )
    elif latest_age > FRESH_AFTER_SECONDS:
        status = "warning"
        summary = (
            "Market database is reachable, but its "
            "latest observation may be stale."
        )
    else:
        status = "healthy"
        summary = (
            "Market database is reachable and "
            "observations are current."
        )

    return {
        "status": status,
        "summary": summary,
        "reachable": True,
        "statistics": statistics,
    }


def _backup_health() -> dict:
    try:
        return get_market_backup_status()

    except Exception as error:
        return {
            "status": "critical",
            "summary": (
                "Market backup health could not "
                "be checked."
            ),
            "error": str(error),
            "backup_count": 0,
            "timer_active": False,
            "latest_backup": None,
        }


def get_market_operations() -> dict:
    generated_at = _utc_now()

    database = _database_health()

    database_statistics = (
        database.get("statistics") or {}
    )

    collector = _collector_health(
        database_statistics
    )

    backup = _backup_health()

    component_statuses = [
        collector["status"],
        database["status"],
        backup["status"],
    ]

    if "critical" in component_statuses:
        status = "critical"
        summary = (
            "One or more Market Intelligence "
            "operations components are critical."
        )

    elif any(
        state in {"warning", "unavailable"}
        for state in component_statuses
    ):
        status = "warning"
        summary = (
            "Market Intelligence is operating "
            "with one or more warnings."
        )

    else:
        status = "healthy"
        summary = (
            "Market Intelligence collection, "
            "storage, and backups are healthy."
        )

    return {
        "status": status,
        "generated_at": generated_at.isoformat(),
        "summary": summary,
        "collector": collector,
        "database": database,
        "backup": backup,
    }
