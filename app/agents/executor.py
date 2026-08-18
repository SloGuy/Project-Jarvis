import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.agents.registry import (
    get_agent,
)
from app.agents.tasks import (
    AgentTask,
    TaskStatus,
    claim_task,
    complete_task,
    fail_task,
    get_tasks_for_agent,
)
from app.agents.approvals import (
    ApprovalStatus,
    get_approval,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class AgentExecutionError(RuntimeError):
    pass


@dataclass
class ExecutionResult:
    status: str
    capability: str
    output: str


READ_ONLY_CAPABILITIES = {
    "inspect_code",
    "search_code",
    "inspect_git_diff",
    "run_tests",
}


def _run_command(
    command: list[str],
) -> str:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    output = (
        completed.stdout
        + completed.stderr
    ).strip()

    if completed.returncode != 0:
        raise AgentExecutionError(
            output
            or (
                "Command failed with return code "
                f"{completed.returncode}."
            )
        )

    return output


def _validate_relative_path(
    value: str,
) -> Path:
    requested = Path(
        value.strip()
    )

    if requested.is_absolute():
        raise AgentExecutionError(
            "Absolute paths are not allowed."
        )

    resolved = (
        PROJECT_ROOT
        / requested
    ).resolve()

    try:
        resolved.relative_to(
            PROJECT_ROOT
        )
    except ValueError as error:
        raise AgentExecutionError(
            "Path escapes the Jarvis project root."
        ) from error

    return resolved


def inspect_code(
    path: str,
) -> ExecutionResult:
    resolved = _validate_relative_path(
        path
    )

    if not resolved.exists():
        raise AgentExecutionError(
            f"Path does not exist: {path}"
        )

    if not resolved.is_file():
        raise AgentExecutionError(
            f"Path is not a file: {path}"
        )

    try:
        content = resolved.read_text(
            encoding="utf-8"
        )
    except UnicodeDecodeError as error:
        raise AgentExecutionError(
            "File is not readable as UTF-8 text."
        ) from error

    return ExecutionResult(
        status="success",
        capability="inspect_code",
        output=content,
    )


def search_code(
    query: str,
) -> ExecutionResult:
    normalized_query = (
        query.strip()
    )

    if not normalized_query:
        raise AgentExecutionError(
            "Search query is required."
        )

    output = _run_command(
        [
            "grep",
            "-Rni",
            "--exclude=*.pyc",
            "--exclude-dir=.git",
            "--exclude-dir=.venv",
            normalized_query,
            "app",
        ]
    )

    return ExecutionResult(
        status="success",
        capability="search_code",
        output=output,
    )


def inspect_git_diff() -> ExecutionResult:
    output = _run_command(
        [
            "git",
            "diff",
            "--",
            ".",
        ]
    )

    return ExecutionResult(
        status="success",
        capability="inspect_git_diff",
        output=output,
    )


def run_tests(
    target: str = "app/agents",
) -> ExecutionResult:
    resolved = _validate_relative_path(
        target
    )

    if not resolved.exists():
        raise AgentExecutionError(
            f"Test target does not exist: {target}"
        )

    relative_target = str(
        resolved.relative_to(
            PROJECT_ROOT
        )
    )

    if resolved.is_file():
        if resolved.suffix != ".py":
            raise AgentExecutionError(
                "Only Python files can be compiled."
            )

        output = _run_command(
            [
                "python",
                "-m",
                "py_compile",
                relative_target,
            ]
        )

    elif resolved.is_dir():
        output = _run_command(
            [
                "python",
                "-m",
                "compileall",
                "-q",
                relative_target,
            ]
        )

    else:
        raise AgentExecutionError(
            "Test target must be a file or directory."
        )

    return ExecutionResult(
        status="success",
        capability="run_tests",
        output=(
            output
            or (
                "Compilation passed for "
                f"{relative_target}."
            )
        ),
    )


def propose_code_changes(
    *,
    approval_id: str,
    path: str,
    proposal: str,
) -> ExecutionResult:
    approval = get_approval(
        approval_id
    )

    if approval is None:
        raise AgentExecutionError(
            f"Approval not found: {approval_id}"
        )

    if (
        approval.status
        != ApprovalStatus.APPROVED
    ):
        raise AgentExecutionError(
            "Code-change proposal requires "
            "an approved approval request."
        )

    if (
        approval.action
        != "propose_code_changes"
    ):
        raise AgentExecutionError(
            "Approval does not authorize "
            "propose_code_changes."
        )

    resolved = _validate_relative_path(
        path
    )

    if not resolved.exists():
        raise AgentExecutionError(
            f"Path does not exist: {path}"
        )

    normalized_proposal = (
        proposal.strip()
    )

    if not normalized_proposal:
        raise AgentExecutionError(
            "Proposal text is required."
        )

    output = (
        f"Approved proposal for {path}\n\n"
        f"{normalized_proposal}"
    )

    return ExecutionResult(
        status="success",
        capability="propose_code_changes",
        output=output,
    )


def execute_capability(
    *,
    agent_id: str,
    capability: str,
    parameters: dict | None = None,
) -> ExecutionResult:
    agent = get_agent(
        agent_id
    )

    if agent is None:
        raise AgentExecutionError(
            f"Agent not found: {agent_id}"
        )

    if (
        capability
        not in agent.capabilities
    ):
        raise AgentExecutionError(
            "Agent does not have capability: "
            f"{capability}"
        )

    enabled_capabilities = (
        READ_ONLY_CAPABILITIES
        | {
            "propose_code_changes",
        }
    )

    if (
        capability
        not in enabled_capabilities
    ):
        raise AgentExecutionError(
            "Capability is not enabled for "
            "Agent Infrastructure V1 execution."
        )

    parameters = parameters or {}

    if capability == "inspect_code":
        return inspect_code(
            path=str(
                parameters.get(
                    "path",
                    ""
                )
            )
        )

    if capability == "search_code":
        return search_code(
            query=str(
                parameters.get(
                    "query",
                    ""
                )
            )
        )

    if capability == "inspect_git_diff":
        return inspect_git_diff()

    if capability == "run_tests":
        return run_tests(
            target=str(
                parameters.get(
                    "target",
                    "app/agents",
                )
            )
        )

    if capability == "propose_code_changes":
        return propose_code_changes(
            approval_id=str(
                parameters.get(
                    "approval_id",
                    "",
                )
            ),
            path=str(
                parameters.get(
                    "path",
                    "",
                )
            ),
            proposal=str(
                parameters.get(
                    "proposal",
                    "",
                )
            ),
        )

    raise AgentExecutionError(
        f"Unsupported capability: {capability}"
    )


def execute_task(
    *,
    task: AgentTask,
    capability: str,
    parameters: dict | None = None,
) -> AgentTask:
    if task.assigned_agent_id is None:
        raise AgentExecutionError(
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
        raise AgentExecutionError(
            "Only queued or running tasks "
            "can be executed."
        )

    try:
        result = execute_capability(
            agent_id=(
                task.assigned_agent_id
            ),
            capability=capability,
            parameters=parameters,
        )

    except Exception as error:
        fail_task(
            task_id=task.task_id,
            error=str(error),
        )

        raise

    return complete_task(
        task_id=task.task_id,
        result=result.output,
    )


def run_next_task(
    *,
    agent_id: str,
    capability: str,
    parameters: dict | None = None,
) -> AgentTask | None:
    queued_tasks = (
        get_tasks_for_agent(
            agent_id,
            status=TaskStatus.QUEUED,
        )
    )

    if not queued_tasks:
        return None

    task = queued_tasks[-1]

    return execute_task(
        task=task,
        capability=capability,
        parameters=parameters,
    )
