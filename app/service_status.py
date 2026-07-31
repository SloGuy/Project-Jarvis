from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from typing import Any


COMMAND_TIMEOUT_SECONDS = 5

MONITORED_SERVICES = {
    "jarvis_core": {
        "unit": "jarvis-core.service",
        "display_name": "Jarvis Core",
        "critical": True,
    },
    "ollama": {
        "unit": "ollama.service",
        "display_name": "Ollama",
        "critical": True,
    },
    "docker": {
        "unit": "docker.service",
        "display_name": "Docker",
        "critical": True,
    },
    "ssh": {
        "unit": "ssh.service",
        "display_name": "SSH",
        "critical": False,
    },
}


def _utc_now() -> str:
    """Return the current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _run_systemctl(
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    """Run a read-only systemctl command with a strict timeout."""
    return subprocess.run(
        ["/usr/bin/systemctl", *arguments],
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
    )


def _get_service_details(
    service_key: str,
    configuration: dict[str, Any],
) -> dict[str, Any]:
    """Collect structured status information for one systemd service."""
    unit = configuration["unit"]

    try:
        result = _run_systemctl(
            "show",
            unit,
            "--no-page",
            "--property=LoadState",
            "--property=ActiveState",
            "--property=SubState",
            "--property=UnitFileState",
            "--property=MainPID",
            "--property=NRestarts",
            "--property=ActiveEnterTimestamp",
        )
    except FileNotFoundError:
        return {
            "key": service_key,
            "name": configuration["display_name"],
            "unit": unit,
            "critical": configuration["critical"],
            "installed": None,
            "active": None,
            "healthy": False,
            "error": "systemctl was not found.",
        }
    except subprocess.TimeoutExpired:
        return {
            "key": service_key,
            "name": configuration["display_name"],
            "unit": unit,
            "critical": configuration["critical"],
            "installed": None,
            "active": None,
            "healthy": False,
            "error": "The systemd status check timed out.",
        }
    except OSError as error:
        return {
            "key": service_key,
            "name": configuration["display_name"],
            "unit": unit,
            "critical": configuration["critical"],
            "installed": None,
            "active": None,
            "healthy": False,
            "error": str(error),
        }

    properties: dict[str, str] = {}

    for line in result.stdout.splitlines():
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        properties[key] = value

    load_state = properties.get("LoadState", "unknown")
    active_state = properties.get("ActiveState", "unknown")
    sub_state = properties.get("SubState", "unknown")
    unit_file_state = properties.get("UnitFileState", "unknown")

    installed = load_state == "loaded"
    active = active_state == "active"
    healthy = installed and active and sub_state == "running"

    error_message = None

    if result.returncode != 0:
        error_message = (
            result.stderr.strip()
            or f"systemctl exited with status {result.returncode}."
        )

    return {
        "key": service_key,
        "name": configuration["display_name"],
        "unit": unit,
        "critical": configuration["critical"],
        "installed": installed,
        "enabled": unit_file_state == "enabled",
        "load_state": load_state,
        "active": active,
        "active_state": active_state,
        "sub_state": sub_state,
        "healthy": healthy,
        "main_pid": int(properties.get("MainPID", "0") or 0),
        "restart_count": int(properties.get("NRestarts", "0") or 0),
        "active_since": (
            properties.get("ActiveEnterTimestamp") or None
        ),
        "error": error_message,
    }


def get_service_status() -> dict[str, Any]:
    """Return live status for the services required by Jarvis."""
    checked_at = _utc_now()

    services = [
        _get_service_details(service_key, configuration)
        for service_key, configuration in MONITORED_SERVICES.items()
    ]

    installed_services = sum(
        1 for service in services if service["installed"] is True
    )
    active_services = sum(
        1 for service in services if service["active"] is True
    )
    healthy_services = sum(
        1 for service in services if service["healthy"] is True
    )

    critical_failures = [
        service
        for service in services
        if service["critical"] and not service["healthy"]
    ]

    warnings = [
        service
        for service in services
        if not service["critical"] and not service["healthy"]
    ]

    if critical_failures:
        overall_status = "critical"
        summary = (
            f"{len(critical_failures)} critical Jarvis service(s) "
            "are not healthy."
        )
    elif warnings:
        overall_status = "warning"
        summary = (
            "All critical Jarvis services are healthy, but "
            f"{len(warnings)} non-critical service warning(s) were found."
        )
    else:
        overall_status = "healthy"
        summary = (
            f"All {healthy_services} monitored Jarvis services are healthy."
        )

    return {
        "status": overall_status,
        "checked_at": checked_at,
        "summary": summary,
        "counts": {
            "monitored": len(services),
            "installed": installed_services,
            "active": active_services,
            "healthy": healthy_services,
            "critical_failures": len(critical_failures),
            "warnings": len(warnings),
        },
        "services": services,
        "attention_required": [
            {
                "name": service["name"],
                "unit": service["unit"],
                "critical": service["critical"],
                "active_state": service.get("active_state"),
                "sub_state": service.get("sub_state"),
                "error": service.get("error"),
            }
            for service in services
            if not service["healthy"]
        ],
    }
