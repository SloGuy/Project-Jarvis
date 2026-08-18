import difflib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

STATE_DIRECTORY = (
    PROJECT_ROOT
    / "runtime"
    / "agents"
)

PATCH_STATE_FILE = (
    STATE_DIRECTORY
    / "patches.json"
)


@dataclass
class CodePatch:
    patch_id: str
    task_id: str
    agent_id: str
    path: str
    description: str
    original_content: str
    proposed_content: str
    diff: str
    created_at: str
    applied: bool = False
    applied_at: str | None = None


def utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _load_state() -> dict:
    try:
        with PATCH_STATE_FILE.open(
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

    return {
        "patches": {},
    }


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

    with PATCH_STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            state,
            handle,
            indent=2,
        )


def _validate_relative_path(
    value: str,
) -> Path:
    requested = Path(
        value.strip()
    )

    if requested.is_absolute():
        raise ValueError(
            "Absolute paths are not allowed."
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
        raise ValueError(
            "Path escapes the Jarvis project root."
        ) from error

    if not resolved.exists():
        raise ValueError(
            f"Path does not exist: {value}"
        )

    if not resolved.is_file():
        raise ValueError(
            f"Path is not a file: {value}"
        )

    return resolved


def _build_diff(
    *,
    path: str,
    original_content: str,
    proposed_content: str,
) -> str:
    diff_lines = difflib.unified_diff(
        original_content.splitlines(
            keepends=True
        ),
        proposed_content.splitlines(
            keepends=True
        ),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )

    return "".join(
        diff_lines
    )


def create_patch(
    *,
    task_id: str,
    agent_id: str,
    path: str,
    proposed_content: str,
    description: str,
) -> CodePatch:
    if not task_id.strip():
        raise ValueError(
            "task_id is required"
        )

    if not agent_id.strip():
        raise ValueError(
            "agent_id is required"
        )

    normalized_description = (
        description.strip()
    )

    if not normalized_description:
        raise ValueError(
            "description is required"
        )

    resolved = _validate_relative_path(
        path
    )

    normalized_proposed_content = (
        proposed_content
    )

    if not normalized_proposed_content:
        raise ValueError(
            "proposed_content is required"
        )

    original_content = (
        resolved.read_text(
            encoding="utf-8"
        )
    )

    relative_path = str(
        resolved.relative_to(
            PROJECT_ROOT
        )
    )

    diff = _build_diff(
        path=relative_path,
        original_content=original_content,
        proposed_content=(
            normalized_proposed_content
        ),
    )

    if not diff:
        raise ValueError(
            "Proposed content does not change the file."
        )

    patch = CodePatch(
        patch_id=(
            "patch_"
            + uuid.uuid4().hex[:12]
        ),
        task_id=task_id.strip(),
        agent_id=agent_id.strip(),
        path=relative_path,
        description=(
            normalized_description
        ),
        original_content=(
            original_content
        ),
        proposed_content=(
            normalized_proposed_content
        ),
        diff=diff,
        created_at=utc_now_iso(),
    )

    state = _load_state()

    patches = state.setdefault(
        "patches",
        {},
    )

    patches[
        patch.patch_id
    ] = asdict(
        patch
    )

    _save_state(
        state
    )

    return patch


def get_patch(
    patch_id: str,
) -> CodePatch | None:
    state = _load_state()

    record = (
        state
        .get(
            "patches",
            {},
        )
        .get(
            patch_id
        )
    )

    if record is None:
        return None

    return CodePatch(
        **record
    )


def get_patches() -> tuple[
    CodePatch,
    ...,
]:
    state = _load_state()

    records = list(
        state
        .get(
            "patches",
            {},
        )
        .values()
    )

    patches = [
        CodePatch(
            **record
        )
        for record in records
    ]

    patches.sort(
        key=lambda patch: (
            patch.created_at
        ),
        reverse=True,
    )

    return tuple(
        patches
    )


def mark_patch_applied(
    patch_id: str,
) -> CodePatch:
    state = _load_state()

    patches = state.setdefault(
        "patches",
        {},
    )

    record = patches.get(
        patch_id
    )

    if record is None:
        raise ValueError(
            f"Patch not found: {patch_id}"
        )

    patch = CodePatch(
        **record
    )

    if patch.applied:
        raise ValueError(
            "Patch is already marked as applied."
        )

    patch.applied = True
    patch.applied_at = (
        utc_now_iso()
    )

    patches[
        patch.patch_id
    ] = asdict(
        patch
    )

    _save_state(
        state
    )

    return patch


def get_patch_snapshot() -> dict:
    patches = get_patches()

    return {
        "status": "success",
        "patch_count": len(
            patches
        ),
        "applied_count": sum(
            1
            for patch in patches
            if patch.applied
        ),
        "pending_count": sum(
            1
            for patch in patches
            if not patch.applied
        ),
        "patches": [
            {
                "patch_id": patch.patch_id,
                "task_id": patch.task_id,
                "agent_id": patch.agent_id,
                "path": patch.path,
                "description": (
                    patch.description
                ),
                "diff": patch.diff,
                "created_at": (
                    patch.created_at
                ),
                "applied": patch.applied,
                "applied_at": (
                    patch.applied_at
                ),
            }
            for patch in patches
        ],
    }


if __name__ == "__main__":
    print(
        json.dumps(
            get_patch_snapshot(),
            indent=2,
        )
    )
