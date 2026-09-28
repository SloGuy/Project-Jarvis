"""Select revision work for the serialized Capital worker cycle."""

import json

from app.agents.capital_registry import RESEARCH_AGENT_ID
from app.agents.tasks import TaskStatus, create_task, get_tasks
from app.capital.autonomy_control import read_operating_policy
from app.capital.autonomy_policy import authorize_capital_action
from app.capital.research_models import ResearchCandidate, ResearchStatus
from app.capital.research_store import locked_research_state


BUSY_STATUSES = {
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
    TaskStatus.REVIEWING,
}


def schedule_research_revision():
    """Queue at most one new research task per worker cycle.

    The worker must serialize scheduler cycles across processes.
    Existing task identities prevent repeated scheduling of the same
    parent hypothesis. Failed tasks require bounded retry handling;
    scheduling does not replace them with fresh task identities.

    Explicitly rejected research is not automatically reopened.
    """
    policy = read_operating_policy()
    if not policy.enabled:
        return {"status": "disabled", "scheduled_count": 0}
    if policy.paused:
        return {"status": "paused", "scheduled_count": 0}

    authorize_capital_action(
        agent_id=RESEARCH_AGENT_ID,
        action="capital.revise_research",
        policy=policy,
        execution_mode="paper",
    )

    current_tasks = get_tasks()
    busy = [
        task for task in current_tasks
        if task.assigned_agent_id == RESEARCH_AGENT_ID
        and task.status in BUSY_STATUSES
    ]
    if busy:
        return {
            "status": "work_pending",
            "scheduled_count": 0,
            "task_ids": sorted(task.task_id for task in busy),
        }

    with locked_research_state() as state:
        candidates = [
            ResearchCandidate.from_dict(row)
            for row in state["candidates"].values()
        ]

    candidates = sorted(
        (
            candidate for candidate in candidates
            if candidate.status == ResearchStatus.REVISION_REQUIRED
        ),
        key=lambda candidate: (
            candidate.updated_at,
            candidate.research_id,
        ),
    )
    existing_ids = {task.task_id for task in current_tasks}
    previous_requests = []

    for candidate in candidates:
        objective = (
            f"Revise research {candidate.research_id}, hypothesis version "
            f"{candidate.hypothesis_version}. Propose a falsifiable question "
            "using its recorded outcomes and concerns. A small sample "
            "increases uncertainty; it does not explain away losses. "
            "Do not assume more observations improve returns. Preserve "
            "the existing cost assumptions and success criteria."
        )
        instruction = {
            "action": "capital.revise_research",
            "parent_research_id": candidate.research_id,
            "objective": objective,
        }

        # Recheck controls immediately before adding work.
        authorize_capital_action(
            agent_id=RESEARCH_AGENT_ID,
            action="capital.revise_research",
            policy=read_operating_policy(),
            execution_mode="paper",
        )
        task = create_task(
            title=f"Revise {candidate.strategy_name} research",
            objective=json.dumps(instruction, sort_keys=True),
            assigned_agent_id=RESEARCH_AGENT_ID,
            request_key=(
                f"capital:revision:{candidate.research_id}:"
                f"{candidate.hypothesis_version}"
            ),
        )
        if task.task_id in existing_ids:
            previous_requests.append({
                "task_id": task.task_id,
                "status": task.status.value,
            })
            continue

        return {
            "status": "scheduled",
            "scheduled_count": 1,
            "task_id": task.task_id,
            "parent_research_id": candidate.research_id,
        }

    return {
        "status": "no_new_revision_work",
        "scheduled_count": 0,
        "previous_requests": previous_requests,
    }
