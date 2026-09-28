"""Persistent operator controls for Capital paper automation.

Missing controls mean disabled until deployment enables the worker.
Agents must not receive a capability that changes these controls.
"""

import fcntl
import json
import os
import tempfile
from pathlib import Path

from app.capital.autonomy_policy import CapitalOperatingPolicy


CONTROL_DIRECTORY = (
    Path(__file__).resolve().parents[2]
    / "runtime"
    / "capital"
    / "autonomy"
)


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate control field.")
        result[key] = value
    return result


def _decode(payload):
    expected = {"schema_version", "enabled", "paused", "execution_mode"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("Invalid Capital control structure.")
    if type(payload["schema_version"]) is not int:
        raise ValueError("Invalid control schema version.")
    if payload["schema_version"] != 1:
        raise ValueError("Unsupported control schema version.")
    return CapitalOperatingPolicy(
        enabled=payload["enabled"],
        paused=payload["paused"],
        execution_mode=payload["execution_mode"],
    )


def read_operating_policy(*, directory=None):
    root = Path(directory) if directory is not None else CONTROL_DIRECTORY
    try:
        raw = (root / "control.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        return CapitalOperatingPolicy(enabled=False)

    # Invalid or unreadable controls propagate an error. They never enable
    # operation or cause replacement with defaults.
    return _decode(json.loads(raw, object_pairs_hook=_unique_keys))


def update_operating_policy(*, enabled=None, paused=None, directory=None):
    """Operator-only entry point; omitted settings retain current values."""
    for name, value in (("enabled", enabled), ("paused", paused)):
        if value is not None and type(value) is not bool:
            raise ValueError(f"{name} must be a boolean.")

    root = Path(directory) if directory is not None else CONTROL_DIRECTORY
    root.mkdir(parents=True, exist_ok=True)

    with (root / "control.lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            current = read_operating_policy(directory=root)
            policy = CapitalOperatingPolicy(
                enabled=current.enabled if enabled is None else enabled,
                paused=current.paused if paused is None else paused,
            )
            payload = {
                "schema_version": 1,
                "enabled": policy.enabled,
                "paused": policy.paused,
                "execution_mode": policy.execution_mode,
            }
            descriptor, temporary = tempfile.mkstemp(
                prefix=".control-",
                suffix=".tmp",
                dir=root,
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, sort_keys=True, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())

                os.replace(temporary, root / "control.json")
                directory_fd = os.open(root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                Path(temporary).unlink(missing_ok=True)

            return policy
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
