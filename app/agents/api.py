from fastapi import APIRouter, HTTPException

from app.agents.approvals import (
    ApprovalStatus,
    approve_request,
    get_approval_snapshot,
    reject_request,
)
from app.agents.patches import (
    get_patch_snapshot,
)
from app.agents.registry import (
    get_agent_registry_snapshot,
)
from app.agents.reviews import (
    get_review_snapshot,
)
from app.agents.tasks import (
    get_task_snapshot,
)


router = APIRouter(
    prefix="/agents",
    tags=["agents"],
)


@router.get("")
def agents():
    return get_agent_registry_snapshot()


@router.get("/tasks")
def agent_tasks():
    return get_task_snapshot()

@router.post('/tasks')
def create_agent_task(body: dict):
    from app.agents.tasks import create_task, TaskPriority

    title = body.get('title')
    objective = body.get('objective')
    assigned_agent_id = body.get('assigned_agent_id')
    priority = body.get('priority', 'normal')

    if not isinstance(title, str) or not title.strip():
        raise HTTPException(
            status_code=400,
            detail="Title must be a non-empty string.",
        )
    if not isinstance(objective, str) or not objective.strip():
        raise HTTPException(
            status_code=400,
            detail="Objective must be a non-empty string.",
        )
    if assigned_agent_id is not None and not isinstance(assigned_agent_id, str):
        raise HTTPException(
            status_code=400,
            detail="Assigned agent ID must be a string or null.",
        )

    try:
        priority_enum = TaskPriority(priority)
        task = create_task(
            title=title,
            objective=objective,
            assigned_agent_id=assigned_agent_id,
            priority=priority_enum,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    return {
        "status": "success",
        "task_id": task.task_id,
        "title": task.title,
        "objective": task.objective,
        "assigned_agent_id": task.assigned_agent_id,
        "priority": task.priority.value,
        "task_status": task.status.value,
        "created_at": task.created_at,
    }
@router.get("/approvals")
def agent_approvals():
    return get_approval_snapshot()


@router.get("/reviews")
def agent_reviews():
    return get_review_snapshot()


@router.get("/patches")
def agent_patches():
    return get_patch_snapshot()


@router.post(
    "/approvals/{approval_id}/approve"
)
def approve_agent_action(
    approval_id: str,
):
    try:
        approval = approve_request(
            approval_id=approval_id,
            reason=(
                "Approved through Jarvis Core API."
            ),
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    return {
        "status": "success",
        "approval_id": (
            approval.approval_id
        ),
        "approval_status": (
            approval.status.value
        ),
        "decided_at": (
            approval.decided_at
        ),
    }


@router.post(
    "/approvals/{approval_id}/reject"
)
def reject_agent_action(
    approval_id: str,
):
    try:
        approval = reject_request(
            approval_id=approval_id,
            reason=(
                "Rejected through Jarvis Core API."
            ),
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    return {
        "status": "success",
        "approval_id": (
            approval.approval_id
        ),
        "approval_status": (
            approval.status.value
        ),
        "decided_at": (
            approval.decided_at
        ),
    }
