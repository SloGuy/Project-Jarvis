from app.agents.executor import (
    AgentExecutionError,
    execute_capability,
)
from app.agents.planner import (
    AgentPlanningError,
    plan_task,
)
from app.agents.tasks import (
    AgentTask,
    TaskStatus,
    claim_task,
    complete_task,
    fail_task,
    get_tasks_for_agent,
)


class AgentRunnerError(RuntimeError):
    pass


def run_task(
    task: AgentTask,
) -> AgentTask:
    if task.assigned_agent_id is None:
        raise AgentRunnerError(
            "Task has no assigned agent."
        )

    if task.status == TaskStatus.QUEUED:
        task = claim_task(
            task_id=task.task_id,
            agent_id=task.assigned_agent_id,
        )

    if task.status != TaskStatus.RUNNING:
        raise AgentRunnerError(
            "Only queued or running tasks can be executed."
        )

    try:
        plan = plan_task(
            task
        )

        action_results = []

        for index, action in enumerate(
            plan.actions,
            start=1,
        ):
            result = execute_capability(
                agent_id=task.assigned_agent_id,
                capability=action.capability,
                parameters=action.parameters,
            )

            action_results.append(
                {
                    "step": index,
                    "capability": action.capability,
                    "parameters": action.parameters,
                    "status": result.status,
                    "output": result.output,
                }
            )

        result_lines = [
            f"Plan rationale: {plan.rationale}",
            "",
        ]

        for item in action_results:
            result_lines.extend(
                [
                    (
                        f"Step {item['step']}: "
                        f"{item['capability']}"
                    ),
                    (
                        f"Parameters: "
                        f"{item['parameters']}"
                    ),
                    (
                        f"Status: "
                        f"{item['status']}"
                    ),
                    "Output:",
                    (
                        item["output"]
                        or "(no output)"
                    ),
                    "",
                ]
            )

        return complete_task(
            task_id=task.task_id,
            result="\n".join(
                result_lines
            ).strip(),
        )

    except (
        AgentPlanningError,
        AgentExecutionError,
        Exception,
    ) as error:
        fail_task(
            task_id=task.task_id,
            error=str(error),
        )

        raise


def run_next_planned_task(
    *,
    agent_id: str,
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

    return run_task(
        task
    )
