from app.agents.executor import (
    AgentExecutionError,
    inspect_code,
    run_tests,
)
from app.agents.patches import (
    CodePatch,
    get_patch,
)
from app.agents.registry import (
    get_agent,
)
from app.agents.reviews import (
    ReviewDecision,
    PatchReview,
    create_patch_review,
)


REVIEWER_AGENT_ID = (
    "engineering.reviewer"
)


class ReviewerError(RuntimeError):
    pass


def _validate_reviewer() -> None:
    reviewer = get_agent(
        REVIEWER_AGENT_ID
    )

    if reviewer is None:
        raise ReviewerError(
            "Engineering reviewer agent is not registered."
        )

    if reviewer.role != "reviewer":
        raise ReviewerError(
            "Registered engineering reviewer "
            "does not have reviewer role."
        )


def _review_diff_shape(
    patch: CodePatch,
) -> tuple[
    bool,
    list[str],
]:
    reasons = []

    diff = (
        patch.diff
        or ""
    )

    if not diff.strip():
        reasons.append(
            "Patch does not contain a unified diff."
        )

    if (
        "--- a/"
        not in diff
        or "+++ b/"
        not in diff
    ):
        reasons.append(
            "Patch diff is missing expected "
            "unified-diff file headers."
        )

    if (
        patch.original_content
        == patch.proposed_content
    ):
        reasons.append(
            "Proposed content does not change "
            "the target file."
        )

    return (
        len(reasons) == 0,
        reasons,
    )


def _inspect_target(
    patch: CodePatch,
) -> dict:
    try:
        result = inspect_code(
            patch.path
        )

        return {
            "status": "success",
            "output": result.output,
        }

    except (
        AgentExecutionError,
        Exception,
    ) as error:
        return {
            "status": "failed",
            "output": str(error),
        }


def _verify_current_target(
    patch: CodePatch,
) -> dict:
    path = patch.path.lower()

    if path.endswith(".py"):
        try:
            result = run_tests(
                target=patch.path
            )

            return {
                "status": "success",
                "output": result.output,
            }

        except (
            AgentExecutionError,
            Exception,
        ) as error:
            return {
                "status": "failed",
                "output": str(error),
            }

    text_extensions = (
        ".html",
        ".css",
        ".js",
        ".json",
        ".md",
        ".txt",
    )

    if path.endswith(
        text_extensions
    ):
        try:
            result = inspect_code(
                patch.path
            )

            if not result.output:
                return {
                    "status": "failed",
                    "output": (
                        "Target file is empty."
                    ),
                }

            return {
                "status": "success",
                "output": (
                    "Target exists and is readable "
                    "as UTF-8 text."
                ),
            }

        except (
            AgentExecutionError,
            Exception,
        ) as error:
            return {
                "status": "failed",
                "output": str(error),
            }

    return {
        "status": "failed",
        "output": (
            "Unsupported target type for "
            f"controlled verification: "
            f"{patch.path}"
        ),
    }


def review_patch(
    patch_id: str,
) -> PatchReview:
    _validate_reviewer()

    patch = get_patch(
        patch_id
    )

    if patch is None:
        raise ReviewerError(
            f"Patch not found: {patch_id}"
        )

    if patch.applied:
        raise ReviewerError(
            "Applied patches cannot be reviewed "
            "as pending changes."
        )

    diff_ok, diff_reasons = (
        _review_diff_shape(
            patch
        )
    )

    inspection = (
        _inspect_target(
            patch
        )
    )

    verification = (
        _verify_current_target(
            patch
        )
    )

    reasons = []

    if not diff_ok:
        reasons.extend(
            diff_reasons
        )

    if (
        inspection["status"]
        != "success"
    ):
        reasons.append(
            "Reviewer could not inspect "
            "the target file."
        )

    if (
        verification["status"]
        != "success"
    ):
        reasons.append(
            "Current target failed "
            "verification before patch application."
        )

    if reasons:
        decision = (
            ReviewDecision.FAIL
        )

        summary = (
            "Review failed. "
            + " ".join(
                reasons
            )
        )

    else:
        decision = (
            ReviewDecision.PASS
        )

        summary = (
            "Review passed. "
            "Patch contains a valid unified diff, "
            "the target file is readable, and the "
            "current target passes controlled verification."
        )

    return create_patch_review(
        patch_id=patch.patch_id,
        reviewer_agent_id=(
            REVIEWER_AGENT_ID
        ),
        decision=decision,
        summary=summary,
    )


def review_patch_snapshot(
    patch_id: str,
) -> dict:
    review = review_patch(
        patch_id
    )

    return {
        "status": "success",
        "review_id": (
            review.review_id
        ),
        "patch_id": (
            review.patch_id
        ),
        "reviewer_agent_id": (
            review.reviewer_agent_id
        ),
        "decision": (
            review.decision.value
        ),
        "summary": (
            review.summary
        ),
        "created_at": (
            review.created_at
        ),
    }
