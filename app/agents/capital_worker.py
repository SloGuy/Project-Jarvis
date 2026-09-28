"""Serialized Capital worker cycle for autonomous research revisions."""

import fcntl
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.agents.capital_registry import RESEARCH_AGENT_ID
from app.agents.capital_recovery import recover_capital_research_tasks
from app.agents.capital_runner import run_research_task
from app.agents.capital_scheduler import schedule_research_revision
from app.agents.tasks import TaskStatus, get_tasks
from app.capital.autonomy_control import read_operating_policy


STATE_DIRECTORY = (
    Path(__file__).resolve().parents[2]
    / "runtime"
    / "capital"
    / "autonomy"
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _cycle():
    policy = read_operating_policy()
    if not policy.enabled:
        return {"status": "disabled", "processed_count": 0}
    if policy.paused:
        return {"status": "paused", "processed_count": 0}

    recovery = recover_capital_research_tasks()
    scheduling = schedule_research_revision()
    queued = sorted(
        (
            task for task in get_tasks()
            if task.assigned_agent_id == RESEARCH_AGENT_ID
            and task.status == TaskStatus.QUEUED
        ),
        key=lambda task: (task.created_at, task.task_id),
    )
    if not queued:
        return {
            "status": "idle",
            "processed_count": 0,
            "scheduling": scheduling,
            "recovery": recovery,
        }

    task = queued[0]
    completed = run_research_task(task)
    return {
        "status": "completed",
        "processed_count": 1,
        "task_id": completed.task_id,
        "task_status": completed.status.value,
        "execution_attempt": completed.execution_attempts,
        "scheduling": scheduling,
            "recovery": recovery,
    }


def run_once(*, directory=None):
    """Run one cycle; callers schedule subsequent cycles.

    Task heartbeat and attempt checks live in the research runner.
    The process lock covers both selection and execution.
    """
    root = Path(directory) if directory is not None else STATE_DIRECTORY
    root.mkdir(parents=True, exist_ok=True)
    started_at = _now()

    with (root / "worker.lock").open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(
                lock.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            return {
                "status": "busy",
                "processed_count": 0,
                "started_at": started_at,
                "finished_at": _now(),
                "live_capital_authorized": False,
            }

        try:
            result = _cycle()
            return {
                **result,
                "started_at": started_at,
                "finished_at": _now(),
                "scope": "research_revision_cycle",
                "live_capital_authorized": False,
            }
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def main():
    try:
        result = run_once()
    except Exception as error:
        print(json.dumps({
            "status": "failed",
            "error_type": type(error).__name__,
            "message": "Capital cycle failed; inspect task state and logs.",
            "finished_at": _now(),
            "live_capital_authorized": False,
        }), flush=True)
        return 1

    print(json.dumps(result, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
