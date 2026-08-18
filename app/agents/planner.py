import re
from dataclasses import dataclass

from app.agents.registry import get_agent
from app.agents.tasks import AgentTask


class AgentPlanningError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlannedAction:
    capability: str
    parameters: dict


@dataclass(frozen=True)
class AgentPlan:
    agent_id: str
    task_id: str
    actions: tuple[PlannedAction, ...]
    rationale: str


def _normalize_text(
    value: str,
) -> str:
    return re.sub(
        r"\s+",
        " ",
        value.strip().lower(),
    )


def _extract_project_path(
    text: str,
) -> str | None:
    matches = re.findall(
        (
            r"\bapp/"
            r"[A-Za-z0-9_./-]+"
            r"\.(?:py|html|css|js|json|md|txt)\b"
        ),
        text,
    )

    if not matches:
        return None

    return matches[0]


def _validate_capability(
    *,
    agent_id: str,
    capability: str,
) -> None:
    agent = get_agent(
        agent_id
    )

    if agent is None:
        raise AgentPlanningError(
            f"Agent not found: {agent_id}"
        )

    if capability not in agent.capabilities:
        raise AgentPlanningError(
            "Agent does not have capability: "
            f"{capability}"
        )


def _build_inspection_action(
    *,
    agent_id: str,
    path: str,
) -> PlannedAction:
    capability = "inspect_code"

    _validate_capability(
        agent_id=agent_id,
        capability=capability,
    )

    return PlannedAction(
        capability=capability,
        parameters={
            "path": path,
        },
    )


def _build_search_action(
    *,
    agent_id: str,
    query: str,
) -> PlannedAction:
    capability = "search_code"

    _validate_capability(
        agent_id=agent_id,
        capability=capability,
    )

    return PlannedAction(
        capability=capability,
        parameters={
            "query": query,
        },
    )


def _build_test_action(
    *,
    agent_id: str,
    target: str,
) -> PlannedAction:
    capability = "run_tests"

    _validate_capability(
        agent_id=agent_id,
        capability=capability,
    )

    return PlannedAction(
        capability=capability,
        parameters={
            "target": target,
        },
    )


def _build_git_diff_action(
    *,
    agent_id: str,
) -> PlannedAction:
    capability = "inspect_git_diff"

    _validate_capability(
        agent_id=agent_id,
        capability=capability,
    )

    return PlannedAction(
        capability=capability,
        parameters={},
    )


def plan_task(
    task: AgentTask,
) -> AgentPlan:
    if task.assigned_agent_id is None:
        raise AgentPlanningError(
            "Task has no assigned agent."
        )

    agent_id = (
        task.assigned_agent_id
    )

    agent = get_agent(
        agent_id
    )

    if agent is None:
        raise AgentPlanningError(
            f"Assigned agent does not exist: {agent_id}"
        )

    text = _normalize_text(
        " ".join(
            (
                task.title,
                task.objective,
            )
        )
    )

    actions = []
    rationale = []

    project_path = (
        _extract_project_path(
            text
        )
    )

    inspection_requested = any(
        keyword in text
        for keyword in (
            "inspect",
            "review",
            "read",
            "examine",
        )
    )

    search_requested = any(
        keyword in text
        for keyword in (
            "search",
            "find",
            "locate",
        )
    )

    testing_requested = any(
        keyword in text
        for keyword in (
            "compile",
            "test",
            "verify",
            "validate",
            "check",
        )
    )

    diff_requested = any(
        keyword in text
        for keyword in (
            "git diff",
            "inspect diff",
            "review diff",
            "changes",
        )
    )

    if (
        inspection_requested
        and project_path
    ):
        actions.append(
            _build_inspection_action(
                agent_id=agent_id,
                path=project_path,
            )
        )

        rationale.append(
            "Task requests inspection of a specific "
            "project source file."
        )

    if (
        search_requested
        and project_path is None
    ):
        query = task.objective.strip()

        actions.append(
            _build_search_action(
                agent_id=agent_id,
                query=query,
            )
        )

        rationale.append(
            "Task requests a code search without "
            "specifying a source file."
        )

    if testing_requested:
        test_target = None

        if project_path is None:
            test_target = "app/agents"

        elif project_path.endswith(
            ".py"
        ):
            test_target = project_path

        if test_target is not None:
            actions.append(
                _build_test_action(
                    agent_id=agent_id,
                    target=test_target,
                )
            )

            rationale.append(
                "Task requests Python code verification."
            )

    if diff_requested:
        actions.append(
            _build_git_diff_action(
                agent_id=agent_id,
            )
        )

        rationale.append(
            "Task requests inspection of Git changes."
        )

    if not actions:
        if (
            "inspect_code"
            in agent.capabilities
        ):
            actions.append(
                _build_inspection_action(
                    agent_id=agent_id,
                    path=(
                        "app/agents/registry.py"
                    ),
                )
            )

            rationale.append(
                "No explicit executable action was "
                "identified, so the planner selected "
                "a safe read-only inspection."
            )

        else:
            raise AgentPlanningError(
                "No safe plan could be generated "
                "for this task."
            )

    return AgentPlan(
        agent_id=agent_id,
        task_id=task.task_id,
        actions=tuple(
            actions
        ),
        rationale=" ".join(
            rationale
        ),
    )


def plan_to_dict(
    plan: AgentPlan,
) -> dict:
    return {
        "agent_id": plan.agent_id,
        "task_id": plan.task_id,
        "action_count": len(
            plan.actions
        ),
        "actions": [
            {
                "capability": (
                    action.capability
                ),
                "parameters": (
                    action.parameters
                ),
            }
            for action in plan.actions
        ],
        "rationale": plan.rationale,
    }
