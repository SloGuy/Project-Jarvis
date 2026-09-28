"""Bounded recovery for Capital research tasks only."""

from datetime import datetime, timedelta, timezone

from app.agents import tasks
from app.agents.capital_registry import RESEARCH_AGENT_ID


MAX_EXECUTION_ATTEMPTS = 3
RETRY_DELAY = timedelta(seconds=60)
STALE_AFTER = timedelta(seconds=300)

TRANSIENT_ERRORS = (
    "ResearchModelError:",
    "TimeoutError:",
    "OSError:",
    "ConnectionError:",
)


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def recover_capital_research_tasks(*, now=None):
    """Requeue eligible tasks using the same task and draft identities.

    The worker calls this under its process lock. Task-state changes also
    hold the shared task-store lock, coordinating with other workers.

    This does not replace the shared infrastructure recovery mechanism.
    The Capital runner must enforce the attempt limit before claiming.
    """
    measured = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(measured, datetime) or measured.utcoffset() is None:
        raise ValueError("Recovery time must be timezone-aware.")
    measured = measured.astimezone(timezone.utc)

    recovered = []
    exhausted = []
    changed = False

    with tasks._state_lock():
        state = tasks._load_state_unlocked()
        rows = state.get("tasks", {})

        for task_id, row in list(rows.items()):
            task = tasks._task_from_record(row)
            if task.assigned_agent_id != RESEARCH_AGENT_ID:
                continue

            if (
                task.status == tasks.TaskStatus.QUEUED
                and task.execution_attempts >= MAX_EXECUTION_ATTEMPTS
            ):
                task.status = tasks.TaskStatus.FAILED
                task.completed_at = measured.isoformat()
                task.error = "Capital retry budget exhausted."
                rows[task_id] = tasks._task_to_record(task)
                exhausted.append(task_id)
                changed = True
                continue

            stale = False
            retryable = False

            if task.status == tasks.TaskStatus.RUNNING:
                heartbeat = _timestamp(task.heartbeat_at)
                if heartbeat is None:
                    heartbeat = _timestamp(task.started_at)
                stale = (
                    heartbeat is not None
                    and measured - heartbeat >= STALE_AFTER
                )
                retryable = stale

            elif task.status == tasks.TaskStatus.FAILED:
                completed = _timestamp(task.completed_at)
                retryable = (
                    completed is not None
                    and measured - completed >= RETRY_DELAY
                    and isinstance(task.error, str)
                    and task.error.startswith(TRANSIENT_ERRORS)
                )

            if not retryable:
                continue

            if task.execution_attempts >= MAX_EXECUTION_ATTEMPTS:
                exhausted.append(task_id)
                if stale:
                    task.status = tasks.TaskStatus.FAILED
                    task.completed_at = measured.isoformat()
                    task.error = "Capital retry budget exhausted."
                    rows[task_id] = tasks._task_to_record(task)
                    changed = True
                continue

            history = state.setdefault("capital_retry_history", {})
            if not isinstance(history, dict):
                raise RuntimeError("Invalid Capital retry history.")
            history.setdefault(task_id, []).append({
                "at": measured.isoformat(),
                "previous_attempt": task.execution_attempts,
                "previous_status": task.status.value,
                "previous_error": task.error,
                "reason": "stale_heartbeat" if stale else "transient_failure",
            })

            task.status = tasks.TaskStatus.QUEUED
            task.started_at = None
            task.completed_at = None
            task.heartbeat_at = None
            task.result = None
            task.error = None
            task.recovery_attempts += 1
            rows[task_id] = tasks._task_to_record(task)
            recovered.append(task_id)
            changed = True

        if changed:
            tasks._save_state_unlocked(state)

    return {
        "recovered_task_ids": recovered,
        "exhausted_task_ids": exhausted,
        "maximum_execution_attempts": MAX_EXECUTION_ATTEMPTS,
    }
