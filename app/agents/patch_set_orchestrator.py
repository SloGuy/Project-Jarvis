from app.agents.approvals import (
    ApprovalStatus,
    create_approval_request,
    get_approvals,
)
from app.agents.patch_sets import (
    PatchSet,
    get_patch_set,
)
from app.agents.patches import (
    get_patch,
)
from app.agents.reviewer import (
    review_objective_coverage,
    review_patch_snapshot,
)
from app.agents.reviews import (
    ReviewDecision,
    latest_patch_review,
)


class PatchSetOrchestratorError(
    RuntimeError
):
    pass


def _load_patch_set_children(
    patch_set: PatchSet,
):
    patches = []

    for patch_id in patch_set.patch_ids:
        patch = get_patch(
            patch_id
        )

        if patch is None:
            raise PatchSetOrchestratorError(
                f"Patch not found: {patch_id}"
            )

        patches.append(
            patch
        )

    return tuple(
        patches
    )


def _find_patch_set_approval(
    patch_set: PatchSet,
):
    for approval in get_approvals():
        if (
            approval.action
            == "apply_patch_set"
            and approval.payload.get(
                "patch_set_id"
            )
            == patch_set.patch_set_id
        ):
            return approval

    return None


def _create_patch_set_approval(
    patch_set: PatchSet,
):
    return create_approval_request(
        task_id=patch_set.task_id,
        agent_id=patch_set.agent_id,
        action="apply_patch_set",
        description=(
            "Engineering review passed for all "
            "patches in "
            f"{patch_set.patch_set_id}. "
            "Approve atomic application of the "
            "complete multi-file change."
        ),
        payload={
            "patch_set_id": (
                patch_set.patch_set_id
            ),
            "patch_ids": list(
                patch_set.patch_ids
            ),
        },
    )


def orchestrate_patch_set_review(
    patch_set_id: str,
) -> dict:
    patch_set = get_patch_set(
        patch_set_id
    )

    if patch_set is None:
        raise PatchSetOrchestratorError(
            f"Patch set not found: {patch_set_id}"
        )

    if patch_set.applied:
        return {
            "status": "already_applied",
            "patch_set_id": (
                patch_set.patch_set_id
            ),
        }

    patches = _load_patch_set_children(
        patch_set
    )

    review_results = []

    for patch in patches:
        review_patch_snapshot(
            patch.patch_id,
            check_objective_coverage=False,
        )

        review = latest_patch_review(
            patch.patch_id
        )

        if review is None:
            raise PatchSetOrchestratorError(
                "Reviewer did not produce a review "
                f"for {patch.patch_id}."
            )

        review_results.append(
            {
                "patch_id": patch.patch_id,
                "path": patch.path,
                "review_id": review.review_id,
                "decision": (
                    review.decision.value
                ),
                "summary": review.summary,
            }
        )

    combined_diff = "\n".join(
        patch.diff or ""
        for patch in patches
    )

    objective_reasons = (
        review_objective_coverage(
            task_id=patches[0].task_id,
            diff=combined_diff,
        )
        if patches
        else [
            "Patch set does not contain any patches."
        ]
    )

    if objective_reasons:
        review_results.append(
            {
                "patch_id": None,
                "path": None,
                "review_id": None,
                "decision": (
                    ReviewDecision.FAIL.value
                ),
                "summary": (
                    "Review failed. "
                    + " ".join(
                        objective_reasons
                    )
                ),
            }
        )

    failed_reviews = [
        result
        for result in review_results
        if (
            result["decision"]
            != ReviewDecision.PASS.value
        )
    ]

    if failed_reviews:
        return {
            "status": "review_failed",
            "patch_set_id": (
                patch_set.patch_set_id
            ),
            "reviews": review_results,
            "approval": None,
        }

    approval = _find_patch_set_approval(
        patch_set
    )

    if approval is None:
        approval = _create_patch_set_approval(
            patch_set
        )

    return {
        "status": (
            "approved"
            if (
                approval.status
                == ApprovalStatus.APPROVED
            )
            else "rejected"
            if (
                approval.status
                == ApprovalStatus.REJECTED
            )
            else "awaiting_approval"
        ),
        "patch_set_id": patch_set.patch_set_id,
        "reviews": review_results,
        "approval": {
            "approval_id": (
                approval.approval_id
            ),
            "status": (
                approval.status.value
            ),
            "action": approval.action,
        },
    }
