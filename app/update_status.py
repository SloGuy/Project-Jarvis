from __future__ import annotations

import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


APT_TIMEOUT_SECONDS = 20
CACHE_TTL_SECONDS = 300
MAX_PACKAGE_DETAILS = 50

APT_LISTS_DIRECTORY = Path("/var/lib/apt/lists")
REBOOT_REQUIRED_FILE = Path("/var/run/reboot-required")
REBOOT_PACKAGES_FILE = Path("/var/run/reboot-required.pkgs")

_CACHE_LOCK = Lock()
_CACHE: dict[str, Any] = {
    "created_monotonic": 0.0,
    "data": None,
}

# Example:
# Inst openssl [3.0.2-0ubuntu1] (3.0.2-0ubuntu1.20 Ubuntu:22.04/jammy-security [amd64])
APT_INSTALL_PATTERN = re.compile(
    r"^Inst\s+"
    r"(?P<name>\S+)"
    r"(?:\s+\[(?P<current>[^\]]+)\])?"
    r"\s+\((?P<candidate>\S+)"
    r"(?:\s+(?P<source>.*?))?"
    r"\)$"
)


def _utc_now() -> str:
    """Return the current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a read-only system command with a strict timeout."""
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=APT_TIMEOUT_SECONDS,
        check=False,
    )


def _apt_metadata_age_hours() -> float | None:
    """Return the age of the newest local APT package index."""
    try:
        timestamps = [
            path.stat().st_mtime
            for path in APT_LISTS_DIRECTORY.iterdir()
            if path.is_file() and not path.name.endswith(".lock")
        ]
    except (FileNotFoundError, PermissionError, OSError):
        return None

    if not timestamps:
        return None

    age_seconds = max(0.0, time.time() - max(timestamps))
    return round(age_seconds / 3600, 2)


def _parse_package_updates(output: str) -> list[dict[str, Any]]:
    """Parse simulated APT output into predictable package records."""
    packages: list[dict[str, Any]] = []

    for raw_line in output.splitlines():
        line = raw_line.strip()

        if not line.startswith("Inst "):
            continue

        match = APT_INSTALL_PATTERN.match(line)

        if match:
            package_name = match.group("name")
            current_version = match.group("current")
            candidate_version = match.group("candidate")
            source = (match.group("source") or "").strip()
        else:
            # Preserve basic visibility if Ubuntu changes the output format.
            parts = line.split()
            package_name = parts[1] if len(parts) > 1 else "unknown"
            current_version = None
            candidate_version = None
            source = line

        source_lower = source.lower()
        is_security_update = (
            "-security" in source_lower
            or "/security" in source_lower
            or "security.ubuntu.com" in source_lower
        )

        packages.append(
            {
                "name": package_name,
                "current_version": current_version,
                "candidate_version": candidate_version,
                "security_update": is_security_update,
                "source": source or None,
            }
        )

    packages.sort(
        key=lambda package: (
            not package["security_update"],
            package["name"].lower(),
        )
    )

    return packages


def _get_reboot_status() -> dict[str, Any]:
    """Check whether Ubuntu reports that a reboot is required."""
    reboot_required = REBOOT_REQUIRED_FILE.exists()
    packages: list[str] = []

    if REBOOT_PACKAGES_FILE.exists():
        try:
            packages = sorted(
                {
                    line.strip()
                    for line in REBOOT_PACKAGES_FILE.read_text(
                        encoding="utf-8",
                        errors="replace",
                    ).splitlines()
                    if line.strip()
                }
            )
        except (PermissionError, OSError):
            packages = []

    return {
        "required": reboot_required,
        "packages": packages,
    }


def _build_summary(
    total_updates: int,
    security_updates: int,
    reboot_required: bool,
    metadata_age_hours: float | None,
) -> str:
    """Create a compact, deterministic summary for the local AI model."""
    if total_updates == 0:
        summary = "No Ubuntu package updates are currently listed."
    else:
        summary = f"{total_updates} Ubuntu package update(s) are available."

    if security_updates:
        summary += f" {security_updates} are security update(s)."

    if reboot_required:
        summary += " A system reboot is required."

    if metadata_age_hours is None:
        summary += " APT metadata age could not be determined."
    elif metadata_age_hours > 24:
        summary += (
            f" APT metadata is {metadata_age_hours} hours old; "
            "refresh package lists for the latest results."
        )

    return summary


def _collect_update_status() -> dict[str, Any]:
    """Collect live Ubuntu update information without changing the system."""
    checked_at = _utc_now()
    metadata_age_hours = _apt_metadata_age_hours()
    reboot = _get_reboot_status()

    try:
        result = _run_command(
            [
                "/usr/bin/apt-get",
                "--simulate",
                "-o",
                "Debug::NoLocking=1",
                "upgrade",
            ]
        )
    except FileNotFoundError:
        return {
            "status": "unavailable",
            "checked_at": checked_at,
            "summary": "APT is not installed on this system.",
            "ubuntu": {
                "updates_available": None,
                "security_updates": None,
                "packages": [],
                "package_details_truncated": False,
                "metadata_age_hours": metadata_age_hours,
            },
            "reboot": reboot,
            "errors": ["The apt-get command was not found."],
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "checked_at": checked_at,
            "summary": (
                f"The Ubuntu update check exceeded "
                f"{APT_TIMEOUT_SECONDS} seconds."
            ),
            "ubuntu": {
                "updates_available": None,
                "security_updates": None,
                "packages": [],
                "package_details_truncated": False,
                "metadata_age_hours": metadata_age_hours,
            },
            "reboot": reboot,
            "errors": ["APT update simulation timed out."],
        }
    except OSError as exc:
        return {
            "status": "error",
            "checked_at": checked_at,
            "summary": "The Ubuntu update check could not be executed.",
            "ubuntu": {
                "updates_available": None,
                "security_updates": None,
                "packages": [],
                "package_details_truncated": False,
                "metadata_age_hours": metadata_age_hours,
            },
            "reboot": reboot,
            "errors": [str(exc)],
        }

    packages = _parse_package_updates(result.stdout)
    security_updates = sum(
        1 for package in packages if package["security_update"]
    )
    total_updates = len(packages)

    errors: list[str] = []
    if result.returncode != 0:
        error_message = result.stderr.strip() or (
            f"apt-get exited with status {result.returncode}."
        )
        errors.append(error_message)

    stale_metadata = (
        metadata_age_hours is None or metadata_age_hours > 24
    )

    if errors:
        overall_status = "error"
    elif security_updates > 0 or reboot["required"] or stale_metadata:
        overall_status = "warning"
    else:
        overall_status = "healthy"

    visible_packages = packages[:MAX_PACKAGE_DETAILS]

    return {
        "status": overall_status,
        "checked_at": checked_at,
        "summary": _build_summary(
            total_updates=total_updates,
            security_updates=security_updates,
            reboot_required=reboot["required"],
            metadata_age_hours=metadata_age_hours,
        ),
        "ubuntu": {
            "updates_available": total_updates,
            "security_updates": security_updates,
            "regular_updates": total_updates - security_updates,
            "packages": visible_packages,
            "package_details_truncated": (
                len(packages) > MAX_PACKAGE_DETAILS
            ),
            "metadata_age_hours": metadata_age_hours,
            "metadata_stale": stale_metadata,
        },
        "reboot": reboot,
        "errors": errors,
    }


def get_update_status(force_refresh: bool = False) -> dict[str, Any]:
    """
    Return Ubuntu update intelligence.

    Results are cached briefly to prevent repeated tool calls from running
    the same APT simulation unnecessarily. Set force_refresh=True to bypass
    the cache.
    """
    now = time.monotonic()

    with _CACHE_LOCK:
        cached_data = _CACHE["data"]
        cache_age = now - float(_CACHE["created_monotonic"])

        if (
            not force_refresh
            and cached_data is not None
            and cache_age < CACHE_TTL_SECONDS
        ):
            return {
                **cached_data,
                "cached": True,
                "cache_age_seconds": round(cache_age, 2),
            }

    fresh_data = _collect_update_status()

    with _CACHE_LOCK:
        _CACHE["data"] = fresh_data
        _CACHE["created_monotonic"] = time.monotonic()

    return {
        **fresh_data,
        "cached": False,
        "cache_age_seconds": 0.0,
    }
