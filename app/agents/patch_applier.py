from pathlib import Path

from app.agents.approvals import (
    ApprovalStatus,
    get_approval,
)
from app.agents.executor import (
    AgentExecutionError,
    run_tests,
)
from app.agents.patches import (
    CodePatch,
    get_patch,
    mark_patch_applied,
)
from app.agents.reviews import (
    patch_has_passing_review,
)


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[2]
)


TEXT_VERIFICATION_EXTENSIONS = (
    ".html",
    ".css",
    ".js",
    ".json",
    ".md",
    ".txt",
)


class PatchApplyError(RuntimeError):
    pass


def _resolve_patch_path(
    patch: CodePatch,
) -> Path:
    requested = Path(
        patch.path
    )

    if requested.is_absolute():
        raise PatchApplyError(
            "Absolute patch paths are not allowed."
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
        raise PatchApplyError(
            "Patch path escapes the Jarvis "
            "project root."
        ) from error

    if not resolved.exists():
        raise PatchApplyError(
            f"Patch target does not exist: "
            f"{patch.path}"
        )

    if not resolved.is_file():
        raise PatchApplyError(
            f"Patch target is not a file: "
            f"{patch.path}"
        )

    return resolved


def validate_apply_approval(
    *,
    approval_id: str,
    patch: CodePatch,
) -> None:
    approval = get_approval(
        approval_id
    )

    if approval is None:
        raise PatchApplyError(
            f"Approval not found: "
            f"{approval_id}"
        )

    if (
        approval.status
        != ApprovalStatus.APPROVED
    ):
        raise PatchApplyError(
            "Patch application requires "
            "an approved approval request."
        )

    if approval.action != "apply_patch":
        raise PatchApplyError(
            "Approval does not authorize "
            "patch application."
        )

    approved_patch_id = (
        approval.payload.get(
            "patch_id"
        )
    )

    if approved_patch_id != patch.patch_id:
        raise PatchApplyError(
            "Approval does not authorize "
            f"patch {patch.patch_id}."
        )

    approved_path = (
        approval.payload.get(
            "path"
        )
    )

    if (
        approved_path is not None
        and approved_path != patch.path
    ):
        raise PatchApplyError(
            "Approval path does not match "
            "the patch target."
        )


def verify_patch_precondition(
    patch: CodePatch,
) -> Path:
    resolved = _resolve_patch_path(
        patch
    )

    try:
        current_content = (
            resolved.read_text(
                encoding="utf-8"
            )
        )

    except (
        OSError,
        UnicodeError,
    ) as error:
        raise PatchApplyError(
            "Could not read patch target "
            "before application."
        ) from error

    if (
        current_content
        != patch.original_content
    ):
        raise PatchApplyError(
            "Patch precondition failed. "
            "The target file has changed "
            "since this patch was created."
        )

    return resolved


def verify_applied_patch(
    patch: CodePatch,
) -> dict:
    path = patch.path.lower()

    try:
        resolved = _resolve_patch_path(
            patch
        )

    except Exception as error:
        return {
            "status": "failed",
            "capability": (
                "path_verification"
            ),
            "output": str(error),
        }

    try:
        actual_content = (
            resolved.read_text(
                encoding="utf-8"
            )
        )

    except (
        OSError,
        UnicodeError,
    ) as error:
        return {
            "status": "failed",
            "capability": (
                "content_verification"
            ),
            "output": (
                "Applied target could not "
                f"be read: {error}"
            ),
        }

    if (
        actual_content
        != patch.proposed_content
    ):
        return {
            "status": "failed",
            "capability": (
                "content_verification"
            ),
            "output": (
                "Applied file content does "
                "not match proposed content."
            ),
        }

    if path.endswith(".py"):
        try:
            result = run_tests(
                target=patch.path
            )

            return {
                "status": "success",
                "capability": (
                    result.capability
                ),
                "output": result.output,
            }

        except (
            AgentExecutionError,
            Exception,
        ) as error:
            return {
                "status": "failed",
                "capability": "run_tests",
                "output": str(error),
            }

    if path.endswith(
        TEXT_VERIFICATION_EXTENSIONS
    ):
        return {
            "status": "success",
            "capability": (
                "content_verification"
            ),
            "output": (
                "Applied file exists, is "
                "readable as UTF-8 text, and "
                "matches proposed content."
            ),
        }

    return {
        "status": "failed",
        "capability": (
            "unsupported_verification"
        ),
        "output": (
            "Unsupported target type for "
            "post-application verification: "
            f"{patch.path}"
        ),
    }


def rollback_patch(
    *,
    patch: CodePatch,
    resolved: Path,
) -> dict:
    try:
        resolved.write_text(
            patch.original_content,
            encoding="utf-8",
        )

        restored_content = (
            resolved.read_text(
                encoding="utf-8"
            )
        )

        if (
            restored_content
            != patch.original_content
        ):
            raise PatchApplyError(
                "Rollback verification failed."
            )

        return {
            "status": "success",
            "restored": True,
            "path": patch.path,
        }

    except Exception as error:
        return {
            "status": "failed",
            "restored": False,
            "path": patch.path,
            "error": str(error),
        }


def apply_patch(
    *,
    patch_id: str,
    approval_id: str,
) -> dict:
    patch = get_patch(
        patch_id
    )

    if patch is None:
        raise PatchApplyError(
            f"Patch not found: "
            f"{patch_id}"
        )

    if patch.applied:
        return {
            "status": "already_applied",
            "patch": patch,
            "verification": None,
            "rollback": None,
        }

    if not patch_has_passing_review(
        patch.patch_id
    ):
        raise PatchApplyError(
            "Patch requires a passing "
            "engineering review before "
            "it can be applied."
        )

    validate_apply_approval(
        approval_id=approval_id,
        patch=patch,
    )

    resolved = verify_patch_precondition(
        patch
    )

    try:
        resolved.write_text(
            patch.proposed_content,
            encoding="utf-8",
        )

    except OSError as error:
        raise PatchApplyError(
            "Could not write proposed "
            "patch content."
        ) from error

    verification = verify_applied_patch(
        patch
    )

    if (
        verification["status"]
        != "success"
    ):
        rollback = rollback_patch(
            patch=patch,
            resolved=resolved,
        )

        return {
            "status": (
                "rolled_back"
                if rollback["status"]
                == "success"
                else "rollback_failed"
            ),
            "patch": patch,
            "verification": verification,
            "rollback": rollback,
        }

    updated_patch = mark_patch_applied(
        patch.patch_id
    )

    return {
        "status": "success",
        "patch": updated_patch,
        "verification": verification,
        "rollback": None,
    }
