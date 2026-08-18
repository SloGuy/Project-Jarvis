from app.agents.approvals import (
    ApprovalStatus,
    create_approval_request,
    get_approvals,
)
from app.agents.patches import (
    CodePatch,
    get_patch,
)
from app.agents.reviewer import (
    REVIEWER_AGENT_ID,
    review_patch_snapshot,
)
from app.agents.reviews import (
    ReviewDecision,
    latest_patch_review,
)
from app.agents.tasks import (
    TaskPriority,
    TaskStatus,
    claim_task,
    complete_task,
    create_task,
    fail_task,
    get_tasks_for_agent,
)
from app.agents.patch_applier import (
    apply_patch,
)


class OrchestratorError(RuntimeError):
    pass


def _find_review_task(
    patch: CodePatch,
):
    tasks = get_tasks_for_agent(
        REVIEWER_AGENT_ID
    )

    marker = (
        f"Review patch {patch.patch_id}"
    )

    for task in tasks:
        if marker in task.objective:
            return task

    return None


def _create_review_task(
    patch: CodePatch,
):
    return create_task(
        title=(
            f"Review {patch.patch_id}"
        ),
        objective=(
            f"Review patch {patch.patch_id} "
            f"for task {patch.task_id}. "
            f"Target file: {patch.path}. "
            "Determine whether the patch should "
            "pass engineering review."
        ),
        assigned_agent_id=(
            REVIEWER_AGENT_ID
        ),
        priority=TaskPriority.NORMAL,
    )


def _find_apply_approval(
    patch: CodePatch,
):
    approvals = get_approvals()

    for approval in approvals:
        if (
            approval.action
            == "apply_patch"
            and approval.payload.get(
                "patch_id"
            )
            == patch.patch_id
        ):
            return approval

    return None


def _create_apply_approval(
    patch: CodePatch,
):
    return create_approval_request(
        task_id=patch.task_id,
        agent_id=patch.agent_id,
        action="apply_patch",
        description=(
            "Engineering review passed. "
            f"Approve application of {patch.patch_id} "
            f"to {patch.path}."
        ),
        payload={
            "patch_id": patch.patch_id,
            "path": patch.path,
        },
    )


def orchestrate_patch(
    patch_id: str,
) -> dict:
    patch = get_patch(
        patch_id
    )

    if patch is None:
        raise OrchestratorError(
            f"Patch not found: {patch_id}"
        )

    if patch.applied:
        return {
            "status": "already_applied",
            "patch_id": patch.patch_id,
        }

    review_task = _find_review_task(
        patch
    )

    if review_task is None:
        review_task = _create_review_task(
            patch
        )

    if review_task.status == TaskStatus.QUEUED:
        review_task = claim_task(
            task_id=review_task.task_id,
            agent_id=REVIEWER_AGENT_ID,
        )

    review = latest_patch_review(
        patch.patch_id
    )

    if review is None:
        try:
            review_result = (
                review_patch_snapshot(
                    patch.patch_id
                )
            )

            decision = (
                ReviewDecision(
                    review_result[
                        "decision"
                    ]
                )
            )

            if (
                decision
                == ReviewDecision.PASS
            ):
                review_task = complete_task(
                    task_id=review_task.task_id,
                    result=(
                        review_result[
                            "summary"
                        ]
                    ),
                )

            else:
                review_task = fail_task(
                    task_id=review_task.task_id,
                    error=(
                        review_result[
                            "summary"
                        ]
                    ),
                )

            review = latest_patch_review(
                patch.patch_id
            )

        except Exception as error:
            try:
                fail_task(
                    task_id=review_task.task_id,
                    error=str(error),
                )
            except Exception:
                pass

            raise OrchestratorError(
                "Engineering review failed: "
                f"{error}"
            ) from error

    if review is None:
        raise OrchestratorError(
            "Reviewer did not produce a review."
        )

    if (
        review.decision
        != ReviewDecision.PASS
    ):
        return {
            "status": "review_failed",
            "patch_id": patch.patch_id,
            "review_task_id": (
                review_task.task_id
            ),
            "review_id": (
                review.review_id
            ),
            "review_decision": (
                review.decision.value
            ),
            "review_summary": (
                review.summary
            ),
            "approval": None,
        }

    approval = _find_apply_approval(
        patch
    )

    if approval is None:
        approval = _create_apply_approval(
            patch
        )

    if (
        approval.status
        == ApprovalStatus.APPROVED
    ):
        apply_result = apply_patch(
            patch_id=patch.patch_id,
            approval_id=(
                approval.approval_id
            ),
        )

        return {
            "status": (
                apply_result[
                    "status"
                ]
            ),
            "patch_id": (
                patch.patch_id
            ),
            "review_task_id": (
                review_task.task_id
            ),
            "review_task_status": (
                review_task.status.value
            ),
            "review_id": (
                review.review_id
            ),
            "review_decision": (
                review.decision.value
            ),
            "approval": {
                "approval_id": (
                    approval.approval_id
                ),
                "status": (
                    approval.status.value
                ),
            },
            "application": {
                "patch_applied": (
                    apply_result[
                        "patch"
                    ].applied
                ),
                "verification": (
                    apply_result[
                        "verification"
                    ]
                ),
                "rollback": (
                    apply_result[
                        "rollback"
                    ]
                ),
            },
        }

    return {
        "status": "awaiting_approval",
        "patch_id": patch.patch_id,
        "review_task_id": (
            review_task.task_id
        ),
        "review_task_status": (
            review_task.status.value
        ),
        "review_id": (
            review.review_id
        ),
        "review_decision": (
            review.decision.value
        ),
        "review_summary": (
            review.summary
        ),
        "approval": {
            "approval_id": (
                approval.approval_id
            ),
            "status": (
                approval.status.value
            ),
            "action": (
                approval.action
            ),
        },
    }
