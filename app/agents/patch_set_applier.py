from pathlib import Path

from app.agents.approvals import (
    ApprovalStatus,
    get_approval,
)
from app.agents.patch_applier import (
    PatchApplyError,
    rollback_patch,
    verify_applied_patch,
    verify_patch_precondition,
)
from app.agents.patch_sets import (
    get_patch_set,
    mark_patch_set_applied,
)
from app.agents.patches import (
    get_patch,
    mark_patch_applied,
)
from app.agents.reviews import (
    patch_has_passing_review,
)


class PatchSetApplyError(
    RuntimeError
):
    pass


def _load_patch_set_children(
    patch_set_id: str,
):
    patch_set = get_patch_set(
        patch_set_id
    )

    if patch_set is None:
        raise PatchSetApplyError(
            f"Patch set not found: {patch_set_id}"
        )

    patches = []

    for patch_id in patch_set.patch_ids:
        patch = get_patch(
            patch_id
        )

        if patch is None:
            raise PatchSetApplyError(
                f"Patch not found: {patch_id}"
            )

        patches.append(
            patch
        )

    return (
        patch_set,
        tuple(patches),
    )


def _validate_patch_set_approval(
    *,
    approval_id: str,
    patch_set_id: str,
) -> None:
    approval = get_approval(
        approval_id
    )

    if approval is None:
        raise PatchSetApplyError(
            f"Approval not found: {approval_id}"
        )

    if (
        approval.status
        != ApprovalStatus.APPROVED
    ):
        raise PatchSetApplyError(
            "Patch set application requires "
            "an approved approval request."
        )

    if (
        approval.action
        != "apply_patch_set"
    ):
        raise PatchSetApplyError(
            "Approval does not authorize "
            "PatchSet application."
        )

    approved_patch_set_id = (
        approval.payload.get(
            "patch_set_id"
        )
    )

    if (
        approved_patch_set_id
        != patch_set_id
    ):
        raise PatchSetApplyError(
            "Approval does not authorize "
            f"PatchSet {patch_set_id}."
        )


def apply_patch_set(
    *,
    patch_set_id: str,
    approval_id: str,
) -> dict:
    patch_set, patches = (
        _load_patch_set_children(
            patch_set_id
        )
    )

    if patch_set.applied:
        return {
            "status": "already_applied",
            "patch_set": patch_set,
            "patches": patches,
            "verification": (),
            "rollback": (),
        }

    _validate_patch_set_approval(
        approval_id=approval_id,
        patch_set_id=patch_set.patch_set_id,
    )

    for patch in patches:
        if patch.applied:
            raise PatchSetApplyError(
                "PatchSet contains an already "
                f"applied child patch: {patch.patch_id}"
            )

        if not patch_has_passing_review(
            patch.patch_id
        ):
            raise PatchSetApplyError(
                "Every child patch requires a "
                "passing engineering review. "
                f"Missing pass: {patch.patch_id}"
            )

    resolved_paths: dict[
        str,
        Path,
    ] = {}

    for patch in patches:
        try:
            resolved_paths[
                patch.patch_id
            ] = verify_patch_precondition(
                patch
            )

        except (
            PatchApplyError,
            Exception,
        ) as exc:
            raise PatchSetApplyError(
                "PatchSet precondition failed "
                f"for {patch.patch_id}: {exc}"
            ) from exc

    written = []

    try:
        for patch in patches:
            resolved = resolved_paths[
                patch.patch_id
            ]

            resolved.write_text(
                patch.proposed_content,
                encoding="utf-8",
            )

            written.append(
                patch
            )

    except Exception as exc:
        rollback_results = []

        for written_patch in reversed(
            written
        ):
            rollback_results.append(
                rollback_patch(
                    patch=written_patch,
                    resolved=resolved_paths[
                        written_patch.patch_id
                    ],
                )
            )

        raise PatchSetApplyError(
            "PatchSet write failed and "
            f"rollback was attempted: {exc}"
        ) from exc

    verification_results = []

    verification_failed = False

    for patch in patches:
        verification = (
            verify_applied_patch(
                patch
            )
        )

        verification_results.append(
            {
                "patch_id": patch.patch_id,
                "path": patch.path,
                "verification": verification,
            }
        )

        if (
            verification["status"]
            != "success"
        ):
            verification_failed = True

    if verification_failed:
        rollback_results = []

        for patch in reversed(
            patches
        ):
            rollback_results.append(
                rollback_patch(
                    patch=patch,
                    resolved=resolved_paths[
                        patch.patch_id
                    ],
                )
            )

        return {
            "status": (
                "rolled_back"
                if all(
                    result["status"]
                    == "success"
                    for result in rollback_results
                )
                else "rollback_failed"
            ),
            "patch_set": patch_set,
            "patches": patches,
            "verification": tuple(
                verification_results
            ),
            "rollback": tuple(
                rollback_results
            ),
        }

    updated_patches = []

    for patch in patches:
        updated_patches.append(
            mark_patch_applied(
                patch.patch_id
            )
        )

    updated_patch_set = (
        mark_patch_set_applied(
            patch_set.patch_set_id
        )
    )

    return {
        "status": "success",
        "patch_set": updated_patch_set,
        "patches": tuple(
            updated_patches
        ),
        "verification": tuple(
            verification_results
        ),
        "rollback": (),
    }
