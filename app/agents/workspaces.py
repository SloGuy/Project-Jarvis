import json
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(
    __file__
).resolve().parents[2]

RUNTIME_DIRECTORY = (
    PROJECT_ROOT
    / "runtime"
    / "agents"
    / "workspaces"
)

WORKTREE_DIRECTORY = (
    PROJECT_ROOT.parent
    / "jarvis-agent-worktrees"
)


class WorkspaceError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class EngineeringWorkspace:
    workspace_id: str
    branch_name: str
    path: str
    base_branch: str
    created_at: str


def utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _run_git(
    *args: str,
    cwd: Path | None = None,
) -> str:
    working_directory = (
        cwd
        if cwd is not None
        else PROJECT_ROOT
    )

    try:
        result = subprocess.run(
            [
                "git",
                *args,
            ],
            cwd=working_directory,
            check=True,
            capture_output=True,
            text=True,
        )

    except subprocess.CalledProcessError as exc:
        stderr = (
            exc.stderr.strip()
            if exc.stderr
            else ""
        )

        raise WorkspaceError(
            "Git command failed: "
            f"git {' '.join(args)}"
            + (
                f" | {stderr}"
                if stderr
                else ""
            )
        ) from exc

    return result.stdout.strip()


def _workspace_metadata_path(
    workspace_id: str,
) -> Path:
    return (
        RUNTIME_DIRECTORY
        / f"{workspace_id}.json"
    )


def _save_workspace(
    workspace: EngineeringWorkspace,
) -> None:
    RUNTIME_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    metadata_path = (
        _workspace_metadata_path(
            workspace.workspace_id
        )
    )

    with metadata_path.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            asdict(
                workspace
            ),
            handle,
            indent=2,
        )


def get_workspace(
    workspace_id: str,
) -> EngineeringWorkspace:
    metadata_path = (
        _workspace_metadata_path(
            workspace_id
        )
    )

    if not metadata_path.exists():
        raise WorkspaceError(
            "Workspace not found: "
            f"{workspace_id}"
        )

    with metadata_path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        payload = json.load(
            handle
        )

    return EngineeringWorkspace(
        workspace_id=payload[
            "workspace_id"
        ],
        branch_name=payload[
            "branch_name"
        ],
        path=payload[
            "path"
        ],
        base_branch=payload[
            "base_branch"
        ],
        created_at=payload[
            "created_at"
        ],
    )


def create_workspace(
    *,
    base_branch: str = "main",
) -> EngineeringWorkspace:
    workspace_id = (
        "workspace_"
        + uuid.uuid4().hex[:12]
    )

    branch_name = (
        "agent/"
        + workspace_id
    )

    workspace_path = (
        WORKTREE_DIRECTORY
        / workspace_id
    )

    if workspace_path.exists():
        raise WorkspaceError(
            "Workspace path already exists: "
            f"{workspace_path}"
        )

    WORKTREE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    _run_git(
        "worktree",
        "add",
        "-b",
        branch_name,
        str(
            workspace_path
        ),
        base_branch,
    )

    workspace = EngineeringWorkspace(
        workspace_id=workspace_id,
        branch_name=branch_name,
        path=str(
            workspace_path
        ),
        base_branch=base_branch,
        created_at=utc_now_iso(),
    )

    _save_workspace(
        workspace
    )

    return workspace


def remove_workspace(
    workspace_id: str,
) -> None:
    workspace = get_workspace(
        workspace_id
    )

    workspace_path = Path(
        workspace.path
    )

    if workspace_path.exists():
        _run_git(
            "worktree",
            "remove",
            "--force",
            str(
                workspace_path
            ),
        )

    metadata_path = (
        _workspace_metadata_path(
            workspace_id
        )
    )

    if metadata_path.exists():
        metadata_path.unlink()

    if workspace_path.exists():
        shutil.rmtree(
            workspace_path,
            ignore_errors=True,
        )


def get_workspace_status(
    workspace_id: str,
) -> str:
    workspace = get_workspace(
        workspace_id
    )

    return _run_git(
        "status",
        "--short",
        cwd=Path(
            workspace.path
        ),
    )


def get_workspace_diff(
    workspace_id: str,
) -> str:
    workspace = get_workspace(
        workspace_id
    )

    return _run_git(
        "diff",
        "--no-ext-diff",
        cwd=Path(
            workspace.path
        ),
    )


def get_workspace_untracked_files(
    workspace_id: str,
) -> tuple[str, ...]:
    workspace = get_workspace(
        workspace_id
    )

    output = _run_git(
        "ls-files",
        "--others",
        "--exclude-standard",
        cwd=Path(
            workspace.path
        ),
    )

    if not output:
        return ()

    return tuple(
        line.strip()
        for line in output.splitlines()
        if line.strip()
    )


def _resolve_workspace_file(
    *,
    workspace_id: str,
    relative_path: str,
) -> Path:
    if not isinstance(
        relative_path,
        str,
    ) or not relative_path.strip():
        raise WorkspaceError(
            "Workspace file path is required."
        )

    requested_path = Path(
        relative_path.strip()
    )

    if requested_path.is_absolute():
        raise WorkspaceError(
            "Workspace file path must be relative."
        )

    if (
        requested_path.parts
        and requested_path.parts[0] == ".git"
    ):
        raise WorkspaceError(
            "Direct access to workspace .git "
            "metadata is forbidden."
        )

    workspace = get_workspace(
        workspace_id
    )

    workspace_root = Path(
        workspace.path
    ).resolve()

    resolved_path = (
        workspace_root
        / requested_path
    ).resolve()

    try:
        resolved_path.relative_to(
            workspace_root
        )

    except ValueError as exc:
        raise WorkspaceError(
            "Workspace file path escapes "
            "the workspace boundary."
        ) from exc

    return resolved_path


def read_workspace_file(
    *,
    workspace_id: str,
    relative_path: str,
) -> str:
    resolved_path = _resolve_workspace_file(
        workspace_id=workspace_id,
        relative_path=relative_path,
    )

    if not resolved_path.exists():
        raise WorkspaceError(
            "Workspace file does not exist: "
            f"{relative_path}"
        )

    if not resolved_path.is_file():
        raise WorkspaceError(
            "Workspace path is not a file: "
            f"{relative_path}"
        )

    return resolved_path.read_text(
        encoding="utf-8"
    )


def write_workspace_file(
    *,
    workspace_id: str,
    relative_path: str,
    content: str,
) -> None:
    if not isinstance(
        content,
        str,
    ):
        raise WorkspaceError(
            "Workspace file content must be a string."
        )

    resolved_path = _resolve_workspace_file(
        workspace_id=workspace_id,
        relative_path=relative_path,
    )

    resolved_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    resolved_path.write_text(
        content,
        encoding="utf-8",
    )
