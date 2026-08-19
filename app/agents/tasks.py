import fcntl
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from app.agents.registry import get_agent


PROJECT_ROOT = Path(__file__).resolve().parents[2]

STATE_DIRECTORY = (
    PROJECT_ROOT
    / "runtime"
    / "agents"
)

TASK_STATE_FILE = (
    STATE_DIRECTORY
    / "tasks.json"
)

TASK_LOCK_FILE = (
    STATE_DIRECTORY
    / "tasks.lock"
)

DEFAULT_STALE_TASK_SECONDS = 300
DEFAULT_MAX_RECOVERY_ATTEMPTS = 3


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AgentTask:
    task_id: str
    title: str
    objective: str
    assigned_agent_id: str | None
    priority: TaskPriority
    status: TaskStatus
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    result: str | None = None
    error: str | None = None

    heartbeat_at: str | None = None

    execution_attempts: int = 0
    recovery_attempts: int = 0


def utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


def utc_now_iso() -> str:
    return utc_now().isoformat()


def _parse_datetime(
    value: str | None,
) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            value
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(
        timezone.utc
    )


def _default_state() -> dict:
    return {
        "tasks": {},
    }


def _load_state_unlocked() -> dict:
    try:
        with TASK_STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as handle:
            payload = json.load(
                handle
            )

        if isinstance(payload, dict):
            return payload

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        pass

    return _default_state()


def _save_state_unlocked(
    state: dict,
) -> None:
    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    state["updated_at"] = (
        utc_now_iso()
    )

    file_descriptor, temporary_path = (
        tempfile.mkstemp(
            prefix="tasks_",
            suffix=".json",
            dir=STATE_DIRECTORY,
        )
    )

    try:
        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                state,
                handle,
                indent=2,
            )

            handle.flush()

            os.fsync(
                handle.fileno()
            )

        os.replace(
            temporary_path,
            TASK_STATE_FILE,
        )

    finally:
        if os.path.exists(
            temporary_path
        ):
            os.unlink(
                temporary_path
            )


@contextmanager
def _state_lock():
    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    with TASK_LOCK_FILE.open(
        "a+",
        encoding="utf-8",
    ) as lock_handle:
        fcntl.flock(
            lock_handle.fileno(),
            fcntl.LOCK_EX,
        )

        try:
            yield

        finally:
            fcntl.flock(
                lock_handle.fileno(),
                fcntl.LOCK_UN,
            )


def _load_state() -> dict:
    return _load_state_unlocked()


def _save_state(
    state: dict,
) -> None:
    _save_state_unlocked(
        state
    )


def _task_from_record(
    record: dict,
) -> AgentTask:
    return AgentTask(
        task_id=record["task_id"],
        title=record["title"],
        objective=record["objective"],
        assigned_agent_id=(
            record.get(
                "assigned_agent_id"
            )
        ),
        priority=TaskPriority(
            record.get(
                "priority",
                TaskPriority.NORMAL.value,
            )
        ),
        status=TaskStatus(
            record.get(
                "status",
                TaskStatus.QUEUED.value,
            )
        ),
        created_at=record["created_at"],
        started_at=record.get(
            "started_at"
        ),
        completed_at=record.get(
            "completed_at"
        ),
        result=record.get(
            "result"
        ),
        error=record.get(
            "error"
        ),
        heartbeat_at=record.get(
            "heartbeat_at"
        ),
        execution_attempts=int(
            record.get(
                "execution_attempts",
                0,
            )
        ),
        recovery_attempts=int(
            record.get(
                "recovery_attempts",
                0,
            )
        ),
    )


def _task_to_record(
    task: AgentTask,
) -> dict:
    record = asdict(
        task
    )

    record["priority"] = (
        task.priority.value
    )

    record["status"] = (
        task.status.value
    )

    return record


def create_task(
    *,
    title: str,
    objective: str,
    assigned_agent_id: str | None = None,
    priority: TaskPriority = (
        TaskPriority.NORMAL
    ),
) -> AgentTask:
    normalized_title = (
        title.strip()
    )

    normalized_objective = (
        objective.strip()
    )

    if not normalized_title:
        raise ValueError(
            "title is required"
        )

    if not normalized_objective:
        raise ValueError(
            "objective is required"
        )

    if assigned_agent_id is not None:
        agent = get_agent(
            assigned_agent_id
        )

        if agent is None:
            raise ValueError(
                "Assigned agent does not exist: "
                f"{assigned_agent_id}"
            )

    task = AgentTask(
        task_id=(
            "task_"
            + uuid.uuid4().hex[:12]
        ),
        title=normalized_title,
        objective=normalized_objective,
        assigned_agent_id=(
            assigned_agent_id
        ),
        priority=priority,
        status=TaskStatus.QUEUED,
        created_at=utc_now_iso(),
    )

    with _state_lock():
        state = (
            _load_state_unlocked()
        )

        tasks = state.setdefault(
            "tasks",
            {},
        )

        tasks[
            task.task_id
        ] = _task_to_record(
            task
        )

        _save_state_unlocked(
            state
        )

    return task


def get_task(
    task_id: str,
) -> AgentTask | None:
    state = _load_state()

    record = (
        state
        .get(
            "tasks",
            {},
        )
        .get(
            task_id
        )
    )

    if record is None:
        return None

    return _task_from_record(
        record
    )


def get_tasks() -> tuple[
    AgentTask,
    ...,
]:
    state = _load_state()

    records = list(
        state
        .get(
            "tasks",
            {},
        )
        .values()
    )

    tasks = [
        _task_from_record(
            record
        )
        for record in records
    ]

    tasks.sort(
        key=lambda task: (
            task.created_at
        ),
        reverse=True,
    )

    return tuple(
        tasks
    )


def get_tasks_for_agent(
    agent_id: str,
    status: TaskStatus | None = None,
) -> tuple[
    AgentTask,
    ...,
]:
    normalized_agent_id = (
        agent_id
        .strip()
        .lower()
    )

    tasks = [
        task
        for task in get_tasks()
        if (
            task.assigned_agent_id
            and task.assigned_agent_id.lower()
            == normalized_agent_id
        )
    ]

    if status is not None:
        tasks = [
            task
            for task in tasks
            if task.status == status
        ]

    return tuple(
        tasks
    )


def claim_task(
    *,
    task_id: str,
    agent_id: str,
) -> AgentTask:
    normalized_agent_id = (
        agent_id
        .strip()
        .lower()
    )

    with _state_lock():
        state = (
            _load_state_unlocked()
        )

        tasks = state.setdefault(
            "tasks",
            {},
        )

        record = tasks.get(
            task_id
        )

        if record is None:
            raise ValueError(
                f"Task not found: {task_id}"
            )

        task = _task_from_record(
            record
        )

        assigned_agent_id = (
            task.assigned_agent_id
            or ""
        )

        if (
            assigned_agent_id.lower()
            != normalized_agent_id
        ):
            raise ValueError(
                "Task is not assigned to this agent."
            )

        if (
            task.status
            != TaskStatus.QUEUED
        ):
            raise ValueError(
                "Only queued tasks can be claimed."
            )

        now = utc_now_iso()

        task.status = (
            TaskStatus.RUNNING
        )

        task.started_at = now

        task.heartbeat_at = now

        task.completed_at = None

        task.error = None

        task.execution_attempts += 1

        tasks[
            task.task_id
        ] = _task_to_record(
            task
        )

        _save_state_unlocked(
            state
        )

    return task


def heartbeat_task(
    *,
    task_id: str,
    agent_id: str,
) -> AgentTask:
    normalized_agent_id = (
        agent_id
        .strip()
        .lower()
    )

    with _state_lock():
        state = (
            _load_state_unlocked()
        )

        tasks = state.setdefault(
            "tasks",
            {},
        )

        record = tasks.get(
            task_id
        )

        if record is None:
            raise ValueError(
                f"Task not found: {task_id}"
            )

        task = _task_from_record(
            record
        )

        assigned_agent_id = (
            task.assigned_agent_id
            or ""
        )

        if (
            assigned_agent_id.lower()
            != normalized_agent_id
        ):
            raise ValueError(
                "Task is not assigned to this agent."
            )

        if (
            task.status
            != TaskStatus.RUNNING
        ):
            raise ValueError(
                "Only running tasks can receive a heartbeat."
            )

        task.heartbeat_at = (
            utc_now_iso()
        )

        tasks[
            task.task_id
        ] = _task_to_record(
            task
        )

        _save_state_unlocked(
            state
        )

    return task


def set_task_reviewing(
    *,
    task_id: str,
    result: str,
) -> AgentTask:
    with _state_lock():
        state = (
            _load_state_unlocked()
        )

        tasks = state.setdefault(
            "tasks",
            {},
        )

        record = tasks.get(
            task_id
        )

        if record is None:
            raise ValueError(
                f"Task not found: {task_id}"
            )

        task = _task_from_record(
            record
        )

        if (
            task.status
            != TaskStatus.RUNNING
        ):
            raise ValueError(
                "Only running tasks can enter review."
            )

        now = utc_now_iso()

        task.status = (
            TaskStatus.REVIEWING
        )

        task.completed_at = None

        task.heartbeat_at = now

        task.result = result

        task.error = None

        tasks[
            task.task_id
        ] = _task_to_record(
            task
        )

        _save_state_unlocked(
            state
        )

    return task


def complete_task(
    *,
    task_id: str,
    result: str,
) -> AgentTask:
    with _state_lock():
        state = (
            _load_state_unlocked()
        )

        tasks = state.setdefault(
            "tasks",
            {},
        )

        record = tasks.get(
            task_id
        )

        if record is None:
            raise ValueError(
                f"Task not found: {task_id}"
            )

        task = _task_from_record(
            record
        )

        if (
            task.status
            != TaskStatus.RUNNING
        ):
            raise ValueError(
                "Only running tasks can be completed."
            )

        now = utc_now_iso()

        task.status = (
            TaskStatus.COMPLETED
        )

        task.completed_at = now

        task.heartbeat_at = now

        task.result = result

        task.error = None

        tasks[
            task.task_id
        ] = _task_to_record(
            task
        )

        _save_state_unlocked(
            state
        )

    return task


def complete_reviewing_task(
    *,
    task_id: str,
    result: str,
) -> AgentTask:
    with _state_lock():
        state = (
            _load_state_unlocked()
        )

        tasks = state.setdefault(
            "tasks",
            {},
        )

        record = tasks.get(
            task_id
        )

        if record is None:
            raise ValueError(
                f"Task not found: {task_id}"
            )

        task = _task_from_record(
            record
        )

        if (
            task.status
            != TaskStatus.REVIEWING
        ):
            raise ValueError(
                "Only reviewing tasks can be "
                "completed from review."
            )

        now = utc_now_iso()

        task.status = (
            TaskStatus.COMPLETED
        )

        task.completed_at = now

        task.heartbeat_at = now

        task.result = result

        task.error = None

        tasks[
            task.task_id
        ] = _task_to_record(
            task
        )

        _save_state_unlocked(
            state
        )

    return task


def cancel_reviewing_task(
    *,
    task_id: str,
    reason: str,
) -> AgentTask:
    with _state_lock():
        state = (
            _load_state_unlocked()
        )

        tasks = state.setdefault(
            "tasks",
            {},
        )

        record = tasks.get(
            task_id
        )

        if record is None:
            raise ValueError(
                f"Task not found: {task_id}"
            )

        task = _task_from_record(
            record
        )

        if (
            task.status
            != TaskStatus.REVIEWING
        ):
            raise ValueError(
                "Only reviewing tasks can be cancelled."
            )

        now = utc_now_iso()

        task.status = (
            TaskStatus.CANCELLED
        )

        task.completed_at = now

        task.heartbeat_at = now

        task.error = reason

        tasks[
            task.task_id
        ] = _task_to_record(
            task
        )

        _save_state_unlocked(
            state
        )

    return task


def fail_task(
    *,
    task_id: str,
    error: str,
) -> AgentTask:
    with _state_lock():
        state = (
            _load_state_unlocked()
        )

        tasks = state.setdefault(
            "tasks",
            {},
        )

        record = tasks.get(
            task_id
        )

        if record is None:
            raise ValueError(
                f"Task not found: {task_id}"
            )

        task = _task_from_record(
            record
        )

        if (
            task.status
            != TaskStatus.RUNNING
        ):
            raise ValueError(
                "Only running tasks can be failed."
            )

        now = utc_now_iso()

        task.status = (
            TaskStatus.FAILED
        )

        task.completed_at = now

        task.heartbeat_at = now

        task.error = error

        tasks[
            task.task_id
        ] = _task_to_record(
            task
        )

        _save_state_unlocked(
            state
        )

    return task


def update_task_status(
    *,
    task_id: str,
    status: TaskStatus,
    result: str | None = None,
    error: str | None = None,
) -> AgentTask:
    with _state_lock():
        state = (
            _load_state_unlocked()
        )

        tasks = state.setdefault(
            "tasks",
            {},
        )

        record = tasks.get(
            task_id
        )

        if record is None:
            raise ValueError(
                f"Task not found: {task_id}"
            )

        task = _task_from_record(
            record
        )

        now = utc_now_iso()

        task.status = status

        if (
            status
            == TaskStatus.RUNNING
        ):
            if task.started_at is None:
                task.started_at = now

            task.heartbeat_at = now

        if status == TaskStatus.QUEUED:
            task.started_at = None
            task.completed_at = None
            task.heartbeat_at = None

        if status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            task.completed_at = now
            task.heartbeat_at = now

        if result is not None:
            task.result = result

        if error is not None:
            task.error = error

        tasks[
            task.task_id
        ] = _task_to_record(
            task
        )

        _save_state_unlocked(
            state
        )

    return task


def recover_stale_running_tasks(
    *,
    stale_after_seconds: int = (
        DEFAULT_STALE_TASK_SECONDS
    ),
    max_recovery_attempts: int = (
        DEFAULT_MAX_RECOVERY_ATTEMPTS
    ),
) -> dict:
    if stale_after_seconds < 1:
        raise ValueError(
            "stale_after_seconds must be at least 1."
        )

    if max_recovery_attempts < 0:
        raise ValueError(
            "max_recovery_attempts cannot be negative."
        )

    now = utc_now()

    recovered = []

    failed = []

    with _state_lock():
        state = (
            _load_state_unlocked()
        )

        tasks = state.setdefault(
            "tasks",
            {},
        )

        changed = False

        for task_id, record in list(
            tasks.items()
        ):
            task = _task_from_record(
                record
            )

            if (
                task.status
                != TaskStatus.RUNNING
            ):
                continue

            reference_time = (
                _parse_datetime(
                    task.heartbeat_at
                )
                or _parse_datetime(
                    task.started_at
                )
            )

            if reference_time is None:
                age_seconds = float(
                    "inf"
                )
            else:
                age_seconds = max(
                    0.0,
                    (
                        now
                        - reference_time
                    ).total_seconds(),
                )

            if (
                age_seconds
                < stale_after_seconds
            ):
                continue

            if (
                task.recovery_attempts
                >= max_recovery_attempts
            ):
                task.status = (
                    TaskStatus.FAILED
                )

                task.completed_at = (
                    now.isoformat()
                )

                task.heartbeat_at = (
                    now.isoformat()
                )

                task.error = (
                    "Task exceeded maximum stale-task "
                    "recovery attempts."
                )

                failed.append(
                    task.task_id
                )

            else:
                task.recovery_attempts += 1

                task.status = (
                    TaskStatus.QUEUED
                )

                task.started_at = None

                task.completed_at = None

                task.heartbeat_at = None

                task.error = (
                    "Recovered after stale running state."
                )

                recovered.append(
                    task.task_id
                )

            tasks[
                task_id
            ] = _task_to_record(
                task
            )

            changed = True

        if changed:
            _save_state_unlocked(
                state
            )

    return {
        "status": "success",
        "stale_after_seconds": (
            stale_after_seconds
        ),
        "max_recovery_attempts": (
            max_recovery_attempts
        ),
        "recovered_count": len(
            recovered
        ),
        "recovered_task_ids": (
            recovered
        ),
        "failed_count": len(
            failed
        ),
        "failed_task_ids": (
            failed
        ),
    }


def get_task_snapshot() -> dict:
    tasks = get_tasks()

    status_counts = {
        status.value: 0
        for status in TaskStatus
    }

    for task in tasks:
        status_counts[
            task.status.value
        ] += 1

    return {
        "status": "success",
        "task_count": len(
            tasks
        ),
        "status_counts": (
            status_counts
        ),
        "tasks": [
            _task_to_record(
                task
            )
            for task in tasks
        ],
    }


if __name__ == "__main__":
    print(
        json.dumps(
            get_task_snapshot(),
            indent=2,
        )
    )
