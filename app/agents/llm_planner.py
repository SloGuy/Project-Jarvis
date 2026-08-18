import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.agents.registry import (
    get_agent,
    get_agent_registry_snapshot,
)
from app.agents.tasks import (
    AgentTask,
    TaskPriority,
    create_task,
)


OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://127.0.0.1:11434",
).rstrip("/")

OLLAMA_MODEL = os.getenv(
    "JARVIS_AGENT_PLANNER_MODEL",
    "qwen3:8b",
)

OLLAMA_TIMEOUT_SECONDS = int(
    os.getenv(
        "JARVIS_AGENT_PLANNER_TIMEOUT_SECONDS",
        "120",
    )
)

MAX_PLANNED_TASKS = 5

MAX_PLANNING_ATTEMPTS = 2

MAX_TITLE_LENGTH = 120

MAX_OBJECTIVE_LENGTH = 1200

LLM_ASSIGNABLE_AGENT_IDS = {
    "engineering.software_engineer",
}

FORBIDDEN_TASK_PHRASES = (
    "approve patch",
    "approve changes",
    "apply patch",
    "apply changes",
    "bypass review",
    "skip review",
    "bypass approval",
    "skip approval",
)


class LLMPlanningError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProposedTask:
    title: str
    objective: str
    assigned_agent_id: str
    priority: TaskPriority


@dataclass(frozen=True)
class ProposedPlan:
    objective: str
    rationale: str
    tasks: tuple[ProposedTask, ...]
    model: str


SYSTEM_PROMPT = """
You are the Jarvis Agent Infrastructure planning manager.

Your job is to convert a CEO-level objective into a small,
safe, bounded engineering plan using only the registered agents
provided in the request.

You are a planner, not an executor.

Rules:

1. Return valid JSON only.

2. Never invent an agent. Every assigned_agent_id must exactly
   match an agent_id supplied in the registered agent context.

3. Do not claim that work has already been performed.

4. Do not approve work.

5. Do not apply patches.

6. Do not bypass engineering review or human approval.

7. Do not create shell commands.

8. Do not request unrestricted filesystem access.

9. Prefer inspection and verification before modification.

10. Keep the plan small. Use the minimum number of tasks needed.

11. Maximum tasks: 5.

12. Valid priorities are:
    low
    normal
    high
    critical

13. Use critical priority only for genuinely urgent operational
    or safety failures.

14. Each task must be appropriate for the capabilities of the
    assigned agent.

15. When a task concerns a known Jarvis source file, include the
    exact project-relative path directly in the task objective,
    such as:
    app/templates/agent_command_center.html
    app/agents/planner.py
    app/agents/worker.py

16. Prefer explicit executable objectives over vague management
    language. For inspection tasks, identify the exact file when
    the target is known.

17. The software engineer performs implementation, debugging,
    inspection, searches, and tests.

18. The engineering reviewer reviews proposed code changes.
    Do not assign ordinary implementation work to the reviewer.

19. Do not create a separate review task merely because code
    might change. Jarvis already has a deterministic review gate
    for proposed patches.

Return exactly this JSON structure:

{
    "objective": "normalized objective",
    "rationale": "short explanation of the plan",
    "tasks": [
        {
            "title": "short task title",
            "objective": "specific bounded task objective",
            "assigned_agent_id": "registered.agent_id",
            "priority": "normal"
        }
    ]
}
""".strip()


def _build_agent_context() -> list[dict[str, Any]]:
    snapshot = (
        get_agent_registry_snapshot()
    )

    agents = snapshot.get(
        "agents",
        [],
    )

    return [
        {
            "agent_id": agent.get(
                "agent_id"
            ),
            "name": agent.get(
                "name"
            ),
            "role": agent.get(
                "role"
            ),
            "description": agent.get(
                "description"
            ),
            "capabilities": agent.get(
                "capabilities",
                [],
            ),
            "permissions": agent.get(
                "permissions",
                [],
            ),
        }
        for agent in agents
    ]


def _extract_message_content(
    response_data: dict[str, Any],
) -> str:
    message = response_data.get(
        "message"
    )

    if not isinstance(
        message,
        dict,
    ):
        raise LLMPlanningError(
            "Ollama response did not contain "
            "a message object."
        )

    content = message.get(
        "content"
    )

    if not isinstance(
        content,
        str,
    ):
        raise LLMPlanningError(
            "Ollama response did not contain "
            "message content."
        )

    content = content.strip()

    if not content:
        raise LLMPlanningError(
            "Ollama returned empty planner content."
        )

    return content


def _validate_text(
    *,
    value: Any,
    field_name: str,
    maximum_length: int,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise LLMPlanningError(
            f"{field_name} must be a string."
        )

    normalized = value.strip()

    if not normalized:
        raise LLMPlanningError(
            f"{field_name} cannot be empty."
        )

    if len(normalized) > maximum_length:
        raise LLMPlanningError(
            f"{field_name} exceeds maximum length "
            f"of {maximum_length} characters."
        )

    return normalized


def _validate_priority(
    value: Any,
) -> TaskPriority:
    if not isinstance(
        value,
        str,
    ):
        raise LLMPlanningError(
            "Task priority must be a string."
        )

    try:
        return TaskPriority(
            value.strip().lower()
        )

    except ValueError as exc:
        raise LLMPlanningError(
            f"Unsupported task priority: {value}"
        ) from exc


def _validate_agent(
    agent_id: Any,
):
    if not isinstance(
        agent_id,
        str,
    ):
        raise LLMPlanningError(
            "assigned_agent_id must be a string."
        )

    normalized = (
        agent_id.strip()
    )

    agent = get_agent(
        normalized
    )

    if agent is None:
        raise LLMPlanningError(
            "LLM proposed an unregistered agent: "
            f"{normalized}"
        )

    return agent


def _validate_task(
    payload: Any,
) -> ProposedTask:
    if not isinstance(
        payload,
        dict,
    ):
        raise LLMPlanningError(
            "Each planned task must be an object."
        )

    title = _validate_text(
        value=payload.get(
            "title"
        ),
        field_name="Task title",
        maximum_length=MAX_TITLE_LENGTH,
    )

    objective = _validate_text(
        value=payload.get(
            "objective"
        ),
        field_name="Task objective",
        maximum_length=MAX_OBJECTIVE_LENGTH,
    )

    agent = _validate_agent(
        payload.get(
            "assigned_agent_id"
        )
    )

    if (
        agent.agent_id
        not in LLM_ASSIGNABLE_AGENT_IDS
    ):
        raise LLMPlanningError(
            "LLM planner may not directly assign "
            f"agent: {agent.agent_id}. "
            "Reviewer work is created by the "
            "deterministic review pipeline."
        )

    priority = _validate_priority(
        payload.get(
            "priority",
            "normal",
        )
    )

    task_text = (
        f"{title} {objective}"
        .strip()
        .lower()
    )

    for phrase in FORBIDDEN_TASK_PHRASES:
        if phrase in task_text:
            raise LLMPlanningError(
                "LLM proposed a forbidden task action: "
                f"{phrase}"
            )

    return ProposedTask(
        title=title,
        objective=objective,
        assigned_agent_id=(
            agent.agent_id
        ),
        priority=priority,
    )


def _validate_plan(
    *,
    requested_objective: str,
    payload: Any,
) -> ProposedPlan:
    if not isinstance(
        payload,
        dict,
    ):
        raise LLMPlanningError(
            "Planner output must be a JSON object."
        )

    objective = _validate_text(
        value=payload.get(
            "objective"
        ),
        field_name="Plan objective",
        maximum_length=MAX_OBJECTIVE_LENGTH,
    )

    rationale = _validate_text(
        value=payload.get(
            "rationale"
        ),
        field_name="Plan rationale",
        maximum_length=1200,
    )

    raw_tasks = payload.get(
        "tasks"
    )

    if not isinstance(
        raw_tasks,
        list,
    ):
        raise LLMPlanningError(
            "Plan tasks must be a list."
        )

    if not raw_tasks:
        raise LLMPlanningError(
            "Planner returned no tasks."
        )

    if len(raw_tasks) > MAX_PLANNED_TASKS:
        raise LLMPlanningError(
            "Planner exceeded maximum task count: "
            f"{len(raw_tasks)} > {MAX_PLANNED_TASKS}"
        )

    validated_tasks = []

    for raw_task in raw_tasks:
        if not isinstance(
            raw_task,
            dict,
        ):
            raise LLMPlanningError(
                "Each planned task must be an object."
            )

        raw_agent_id = raw_task.get(
            "assigned_agent_id"
        )

        if (
            isinstance(
                raw_agent_id,
                str,
            )
            and raw_agent_id.strip()
            == "engineering.reviewer"
        ):
            continue

        validated_tasks.append(
            _validate_task(
                raw_task
            )
        )

    if not validated_tasks:
        raise LLMPlanningError(
            "Planner produced no executable "
            "Software Engineer tasks after "
            "deterministic review tasks were removed."
        )

    requested_normalized = (
        requested_objective.strip()
    )

    if not requested_normalized:
        raise LLMPlanningError(
            "Requested objective cannot be empty."
        )

    return ProposedPlan(
        objective=objective,
        rationale=rationale,
        tasks=tuple(
            validated_tasks
        ),
        model=OLLAMA_MODEL,
    )


def _request_plan(
    objective: str,
    validation_feedback: str | None = None,
) -> dict[str, Any]:
    agent_context = (
        _build_agent_context()
    )

    request_context = {
        "objective": objective,
        "registered_agents": (
            agent_context
        ),
    }

    request_body = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": "json",
        "think": False,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    "Create a safe Jarvis engineering "
                    "plan for this objective using only "
                    "the supplied registered agents.\n\n"
                    + (
                        (
                            "Your previous attempt failed "
                            "schema validation with this error:\n"
                            f"{validation_feedback}\n\n"
                            "Return a completely corrected plan. "
                            "Do not repeat malformed task objects. "
                            "Every task must contain string values "
                            "for title, objective, "
                            "assigned_agent_id, and priority.\n\n"
                        )
                        if validation_feedback
                        else ""
                    )
                    + json.dumps(
                        request_context,
                        indent=2,
                        default=str,
                    )
                ),
            },
        ],
        "options": {
            "temperature": 0.1,
            "num_predict": 1200,
        },
    }

    request = Request(
        url=(
            f"{OLLAMA_BASE_URL}/api/chat"
        ),
        data=json.dumps(
            request_body
        ).encode(
            "utf-8"
        ),
        headers={
            "Content-Type": (
                "application/json"
            ),
        },
        method="POST",
    )

    try:
        with urlopen(
            request,
            timeout=OLLAMA_TIMEOUT_SECONDS,
        ) as response:
            response_data = json.loads(
                response.read().decode(
                    "utf-8"
                )
            )

    except HTTPError as exc:
        error_body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise LLMPlanningError(
            f"Ollama returned HTTP {exc.code}: "
            f"{error_body}"
        ) from exc

    except URLError as exc:
        raise LLMPlanningError(
            "Could not connect to Ollama: "
            f"{exc.reason}"
        ) from exc

    except TimeoutError as exc:
        raise LLMPlanningError(
            "Ollama agent planning timed out."
        ) from exc

    except json.JSONDecodeError as exc:
        raise LLMPlanningError(
            "Ollama returned invalid response JSON."
        ) from exc

    message_content = (
        _extract_message_content(
            response_data
        )
    )

    try:
        payload = json.loads(
            message_content
        )

    except json.JSONDecodeError as exc:
        raise LLMPlanningError(
            "Ollama did not return a valid "
            "JSON agent plan."
        ) from exc

    return payload


def propose_plan(
    objective: str,
) -> ProposedPlan:
    normalized_objective = (
        objective.strip()
    )

    if not normalized_objective:
        raise LLMPlanningError(
            "Objective cannot be empty."
        )

    if (
        len(normalized_objective)
        > MAX_OBJECTIVE_LENGTH
    ):
        raise LLMPlanningError(
            "Objective exceeds maximum length "
            f"of {MAX_OBJECTIVE_LENGTH} characters."
        )

    validation_feedback = None
    last_validation_error = None

    for attempt in range(
        1,
        MAX_PLANNING_ATTEMPTS + 1,
    ):
        payload = _request_plan(
            normalized_objective,
            validation_feedback=(
                validation_feedback
            ),
        )

        try:
            return _validate_plan(
                requested_objective=(
                    normalized_objective
                ),
                payload=payload,
            )

        except LLMPlanningError as exc:
            last_validation_error = exc

            if (
                attempt
                >= MAX_PLANNING_ATTEMPTS
            ):
                break

            validation_feedback = str(
                exc
            )

    raise LLMPlanningError(
        "LLM planner failed schema validation "
        f"after {MAX_PLANNING_ATTEMPTS} attempts. "
        "Last validation error: "
        f"{last_validation_error}"
    )


def plan_to_dict(
    plan: ProposedPlan,
) -> dict[str, Any]:
    return {
        "status": "proposed",
        "objective": plan.objective,
        "rationale": plan.rationale,
        "model": plan.model,
        "task_count": len(
            plan.tasks
        ),
        "tasks": [
            {
                "title": task.title,
                "objective": (
                    task.objective
                ),
                "assigned_agent_id": (
                    task.assigned_agent_id
                ),
                "priority": (
                    task.priority.value
                ),
            }
            for task in plan.tasks
        ],
    }


def create_tasks_from_plan(
    plan: ProposedPlan,
) -> tuple[AgentTask, ...]:
    if not isinstance(
        plan,
        ProposedPlan,
    ):
        raise LLMPlanningError(
            "create_tasks_from_plan requires "
            "a validated ProposedPlan."
        )

    created_tasks = []

    for proposed_task in plan.tasks:
        if (
            proposed_task.assigned_agent_id
            not in LLM_ASSIGNABLE_AGENT_IDS
        ):
            raise LLMPlanningError(
                "Validated plan contains an "
                "agent that is not LLM-assignable: "
                f"{proposed_task.assigned_agent_id}"
            )

        task = create_task(
            title=proposed_task.title,
            objective=(
                proposed_task.objective
            ),
            assigned_agent_id=(
                proposed_task.assigned_agent_id
            ),
            priority=(
                proposed_task.priority
            ),
        )

        created_tasks.append(
            task
        )

    return tuple(
        created_tasks
    )


def create_tasks_for_objective(
    objective: str,
) -> dict[str, Any]:
    plan = propose_plan(
        objective
    )

    tasks = create_tasks_from_plan(
        plan
    )

    return {
        "status": "created",
        "objective": (
            plan.objective
        ),
        "rationale": (
            plan.rationale
        ),
        "model": (
            plan.model
        ),
        "task_count": len(
            tasks
        ),
        "tasks": [
            {
                "task_id": (
                    task.task_id
                ),
                "title": (
                    task.title
                ),
                "objective": (
                    task.objective
                ),
                "assigned_agent_id": (
                    task.assigned_agent_id
                ),
                "priority": (
                    task.priority.value
                ),
                "status": (
                    task.status.value
                ),
            }
            for task in tasks
        ],
    }
