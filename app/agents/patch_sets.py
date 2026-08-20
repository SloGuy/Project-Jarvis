import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.agents.patches import (
    CodePatch,
    get_patch,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

STATE_DIRECTORY = (
    PROJECT_ROOT
    / "runtime"
    / "agents"
)

PATCH_SET_STATE_FILE = (
    STATE_DIRECTORY
    / "patch_sets.json"
)


@dataclass
class PatchSet:
    patch_set_id: str
    task_id: str
    agent_id: str
    description: str
    patch_ids: tuple[str, ...]
    combined_diff: str
    created_at: str
    applied: bool = False
    applied_at: str | None = None


def utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _default_state() -> dict:
    return {
        "patch_sets": {},
    }


def _load_state() -> dict:
    try:
        with PATCH_SET_STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as handle:
            payload = json.load(
                handle
            )

        if isinstance(payload, dict):
            return payload

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        pass

    return _default_state()


def _save_state(
    state: dict,
) -> None:
    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    state["updated_at"] = (
        utc_now_iso()
    )

    with PATCH_SET_STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            state,
            handle,
            indent=2,
        )


def _patch_set_to_record(
    patch_set: PatchSet,
) -> dict:
    record = asdict(
        patch_set
    )

    record["patch_ids"] = list(
        patch_set.patch_ids
    )

    return record


def _patch_set_from_record(
    record: dict,
) -> PatchSet:
    return PatchSet(
        patch_set_id=record[
            "patch_set_id"
        ],
        task_id=record[
            "task_id"
        ],
        agent_id=record[
            "agent_id"
        ],
        description=record[
            "description"
        ],
        patch_ids=tuple(
            record.get(
                "patch_ids",
                [],
            )
        ),
        combined_diff=record[
            "combined_diff"
        ],
        created_at=record[
            "created_at"
        ],
        applied=bool(
            record.get(
                "applied",
                False,
            )
        ),
        applied_at=record.get(
            "applied_at"
        ),
    )


def _load_child_patches(
    patch_ids: tuple[str, ...],
) -> tuple[CodePatch, ...]:
    patches = []

    for patch_id in patch_ids:
        patch = get_patch(
            patch_id
        )

        if patch is None:
            raise ValueError(
                f"Patch not found: {patch_id}"
            )

        patches.append(
            patch
        )

    return tuple(
        patches
    )


def create_patch_set(
    *,
    task_id: str,
    agent_id: str,
    description: str,
    patch_ids: tuple[str, ...],
) -> PatchSet:
    normalized_task_id = (
        task_id.strip()
    )

    normalized_agent_id = (
        agent_id.strip()
    )

    normalized_description = (
        description.strip()
    )

    normalized_patch_ids = tuple(
        patch_id.strip()
        for patch_id in patch_ids
        if patch_id.strip()
    )

    if not normalized_task_id:
        raise ValueError(
            "task_id is required"
        )

    if not normalized_agent_id:
        raise ValueError(
            "agent_id is required"
        )

    if not normalized_description:
        raise ValueError(
            "description is required"
        )

    if not normalized_patch_ids:
        raise ValueError(
            "patch_ids must contain at least one patch."
        )

    if (
        len(set(normalized_patch_ids))
        != len(normalized_patch_ids)
    ):
        raise ValueError(
            "patch_ids cannot contain duplicates."
        )

    patches = _load_child_patches(
        normalized_patch_ids
    )

    for patch in patches:
        if (
            patch.task_id
            != normalized_task_id
        ):
            raise ValueError(
                "All patches in a patch set must "
                "belong to the same task."
            )

        if (
            patch.agent_id
            != normalized_agent_id
        ):
            raise ValueError(
                "All patches in a patch set must "
                "belong to the same agent."
            )

        if patch.applied:
            raise ValueError(
                "Applied patches cannot enter "
                "a new patch set."
            )

    combined_diff = "\n".join(
        patch.diff.rstrip()
        for patch in patches
        if patch.diff.strip()
    )

    if not combined_diff.strip():
        raise ValueError(
            "Patch set contains no changes."
        )

    patch_set = PatchSet(
        patch_set_id=(
            "patchset_"
            + uuid.uuid4().hex[:12]
        ),
        task_id=normalized_task_id,
        agent_id=normalized_agent_id,
        description=(
            normalized_description
        ),
        patch_ids=(
            normalized_patch_ids
        ),
        combined_diff=combined_diff,
        created_at=utc_now_iso(),
    )

    state = _load_state()

    patch_sets = state.setdefault(
        "patch_sets",
        {},
    )

    patch_sets[
        patch_set.patch_set_id
    ] = _patch_set_to_record(
        patch_set
    )

    _save_state(
        state
    )

    return patch_set


def get_patch_set(
    patch_set_id: str,
) -> PatchSet | None:
    state = _load_state()

    record = (
        state
        .get(
            "patch_sets",
            {},
        )
        .get(
            patch_set_id
        )
    )

    if record is None:
        return None

    return _patch_set_from_record(
        record
    )


def get_patch_sets() -> tuple[
    PatchSet,
    ...,
]:
    state = _load_state()

    patch_sets = [
        _patch_set_from_record(
            record
        )
        for record in (
            state
            .get(
                "patch_sets",
                {},
            )
            .values()
        )
    ]

    patch_sets.sort(
        key=lambda patch_set: (
            patch_set.created_at
        ),
        reverse=True,
    )

    return tuple(
        patch_sets
    )
