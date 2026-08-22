import json
import re
import signal
import time
from datetime import datetime, timezone

from app.agents.orchestrator import (
    orchestrate_patch,
)
from app.agents.patch_set_orchestrator import (
    orchestrate_patch_set_review,
)
from app.agents.registry import (
    get_agents,
)
from app.agents.runner import (
    run_task,
)
from app.agents.workspace_engineer import (
    create_llm_workspace_patch,
    execute_discovered_multi_file_edit,
)
from app.agents.workspaces import (
    create_workspace,
    remove_workspace,
)
from app.agents.tasks import (
    DEFAULT_MAX_RECOVERY_ATTEMPTS,
    DEFAULT_STALE_TASK_SECONDS,
    TaskStatus,
    claim_task,
    complete_task,
    fail_task,
    get_tasks_for_agent,
    recover_stale_running_tasks,
    set_task_reviewing,
)


POLL_INTERVAL_SECONDS = 5

_worker_running = True


IMPLEMENTATION_KEYWORDS = (
    "add",
    "build",
    "change",
    "create",
    "fix",
    "implement",
    "improve",
    "modify",
    "refactor",
    "remove",
    "replace",
    "update",
)


READ_ONLY_PHRASES = (
    "do not modify any files",
    "do not change any files",
    "do not edit any files",
    "make no changes",
    "read only",
    "read-only",
)


PROJECT_PATH_PATTERN = re.compile(
    (
        r"\bapp/"
        r"[A-Za-z0-9_./-]+"
        r"\.(?:py|html|css|js|json|md|txt)\b"
    )
)


def utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _handle_shutdown(
    signum,
    frame,
) -> None:
    global _worker_running

    _worker_running = False


def _get_worker_agents():
    return tuple(
        agent
        for agent in get_agents()
        if agent.role == "software_engineer"
    )


def _normalize_task_text(
    task,
) -> str:
    return (
        f"{task.title} {task.objective}"
        .strip()
        .lower()
    )


def _extract_task_path(
    task,
) -> str | None:
    text = (
        f"{task.title} {task.objective}"
    )

    matches = (
        PROJECT_PATH_PATTERN.findall(
            text
        )
    )

    if not matches:
        return None

    return matches[0]


def _is_implementation_task(
    task,
) -> bool:
    text = _normalize_task_text(
        task
    )

    if any(
        phrase in text
        for phrase in READ_ONLY_PHRASES
    ):
        return False

    return any(
        keyword in text
        for keyword in IMPLEMENTATION_KEYWORDS
    )


def _run_implementation_task(
    task,
):
    if task.assigned_agent_id is None:
        raise ValueError(
            "Task has no assigned agent."
        )

    if task.status == TaskStatus.QUEUED:
        task = claim_task(
            task_id=task.task_id,
            agent_id=(
                task.assigned_agent_id
            ),
        )

    if task.status != TaskStatus.RUNNING:
        raise ValueError(
            "Only queued or running tasks can "
            "enter the implementation pipeline."
        )

    try:
        path = _extract_task_path(
            task
        )

        workspace = create_workspace(
            base_branch="main",
        )

        try:
            if path is not None:
                workspace_run = (
                    create_llm_workspace_patch(
                        workspace_id=(
                            workspace.workspace_id
                        ),
                        task_id=task.task_id,
                        agent_id=(
                            task.assigned_agent_id
                        ),
                        path=path,
                        objective=task.objective,
                    )
                )

                patch = workspace_run.patch
                patch_set_run = None

            else:
                patch_set_run = (
                    execute_discovered_multi_file_edit(
                        workspace_id=(
                            workspace.workspace_id
                        ),
                        task_id=task.task_id,
                        agent_id=(
                            task.assigned_agent_id
                        ),
                        objective=task.objective,
                        max_targets=3,
                    )
                )

                patch = None

        finally:
            remove_workspace(
                workspace.workspace_id
            )

        if patch is not None:
            orchestration = (
                orchestrate_patch(
                    patch.patch_id
                )
            )

            result = {
                "pipeline": (
                    "software_engineer"
                ),
                "mode": "single_file",
                "patch_id": (
                    patch.patch_id
                ),
                "path": patch.path,
                "patch_applied": (
                    patch.applied
                ),
                "orchestration": (
                    orchestration
                ),
            }

        else:
            patch_set_id = (
                patch_set_run.patch_set_id
            )

            orchestration = (
                orchestrate_patch_set_review(
                    patch_set_id
                )
            )

            result = {
                "pipeline": (
                    "software_engineer"
                ),
                "mode": "multi_file",
                "patch_set_id": (
                    patch_set_id
                ),
                "paths": [
                    run.proposal.path
                    for run
                    in patch_set_run.engineering_runs
                ],
                "orchestration": (
                    orchestration
                ),
            }

        orchestration_status = (
            orchestration.get(
                "status"
            )
        )

        serialized_result = json.dumps(
            result,
            indent=2,
            default=str,
        )

        if (
            orchestration_status
            == "awaiting_approval"
        ):
            return set_task_reviewing(
                task_id=task.task_id,
                result=serialized_result,
            )

        if (
            orchestration_status
            == "review_failed"
        ):
            review_summaries = [
                review.get(
                    "summary",
                    "",
                )
                for review
                in orchestration.get(
                    "reviews",
                    [],
                )
                if review.get(
                    "decision"
                ) != "pass"
            ]

            return fail_task(
                task_id=task.task_id,
                error=(
                    "; ".join(
                        summary
                        for summary
                        in review_summaries
                        if summary
                    )
                    or "Engineering review failed."
                ),
            )

        if (
            orchestration_status
            == "rejected"
        ):
            return fail_task(
                task_id=task.task_id,
                error=(
                    "Engineering change was rejected."
                ),
            )

        if orchestration_status in {
            "success",
            "already_applied",
        }:
            return complete_task(
                task_id=task.task_id,
                result=serialized_result,
            )

        if orchestration_status == "approved":
            return set_task_reviewing(
                task_id=task.task_id,
                result=serialized_result,
            )

        raise ValueError(
            "Unexpected orchestration status: "
            f"{orchestration_status}"
        )

    except Exception as error:
        try:
            fail_task(
                task_id=task.task_id,
                error=str(error),
            )
        except Exception:
            pass

        raise


def _execute_worker_task(
    task,
):
    if _is_implementation_task(
        task
    ):
        return _run_implementation_task(
            task
        )

    return run_task(
        task
    )


def process_agent_queue(
    agent_id: str,
) -> dict:
    queued = get_tasks_for_agent(
        agent_id,
        status=TaskStatus.QUEUED,
    )

    if not queued:
        return {
            "agent_id": agent_id,
            "status": "idle",
            "queued_count": 0,
            "processed_count": 0,
            "results": [],
        }

    results = []

    for task in reversed(
        queued
    ):
        try:
            completed = (
                _execute_worker_task(
                    task
                )
            )

            results.append(
                {
                    "task_id": (
                        task.task_id
                    ),
                    "status": (
                        completed.status.value
                    ),
                    "error": None,
                }
            )

        except Exception as error:
            results.append(
                {
                    "task_id": (
                        task.task_id
                    ),
                    "status": "failed",
                    "error": str(error),
                }
            )

    return {
        "agent_id": agent_id,
        "status": "processed",
        "queued_count": len(
            queued
        ),
        "processed_count": len(
            results
        ),
        "results": results,
    }


def run_worker_cycle() -> dict:
    started_at = utc_now_iso()

    recovery = (
        recover_stale_running_tasks(
            stale_after_seconds=(
                DEFAULT_STALE_TASK_SECONDS
            ),
            max_recovery_attempts=(
                DEFAULT_MAX_RECOVERY_ATTEMPTS
            ),
        )
    )

    agents = _get_worker_agents()

    agent_results = []

    for agent in agents:
        result = process_agent_queue(
            agent.agent_id
        )

        agent_results.append(
            result
        )

    processed_count = sum(
        result.get(
            "processed_count",
            0,
        )
        for result in agent_results
    )

    return {
        "status": "success",
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "recovery": recovery,
        "worker_agent_count": len(
            agents
        ),
        "processed_count": (
            processed_count
        ),
        "agents": agent_results,
    }


def run_worker() -> None:
    global _worker_running

    signal.signal(
        signal.SIGTERM,
        _handle_shutdown,
    )

    signal.signal(
        signal.SIGINT,
        _handle_shutdown,
    )

    print(
        json.dumps(
            {
                "status": "started",
                "started_at": utc_now_iso(),
                "poll_interval_seconds": (
                    POLL_INTERVAL_SECONDS
                ),
                "stale_task_seconds": (
                    DEFAULT_STALE_TASK_SECONDS
                ),
                "max_recovery_attempts": (
                    DEFAULT_MAX_RECOVERY_ATTEMPTS
                ),
                "agents": [
                    agent.agent_id
                    for agent
                    in _get_worker_agents()
                ],
            },
            indent=2,
        ),
        flush=True,
    )

    while _worker_running:
        try:
            result = run_worker_cycle()

            recovery = result.get(
                "recovery",
                {},
            )

            recovery_activity = (
                recovery.get(
                    "recovered_count",
                    0,
                )
                + recovery.get(
                    "failed_count",
                    0,
                )
            )

            if (
                result[
                    "processed_count"
                ]
                > 0
                or recovery_activity > 0
            ):
                print(
                    json.dumps(
                        result,
                        indent=2,
                    ),
                    flush=True,
                )

        except Exception as error:
            print(
                json.dumps(
                    {
                        "status": (
                            "worker_error"
                        ),
                        "observed_at": (
                            utc_now_iso()
                        ),
                        "error": str(
                            error
                        ),
                    },
                    indent=2,
                ),
                flush=True,
            )

        if _worker_running:
            time.sleep(
                POLL_INTERVAL_SECONDS
            )

    print(
        json.dumps(
            {
                "status": "stopped",
                "stopped_at": utc_now_iso(),
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    run_worker()
