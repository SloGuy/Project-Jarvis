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
