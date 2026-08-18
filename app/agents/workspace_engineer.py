from dataclasses import dataclass

from app.agents.workspaces import (
    WorkspaceError,
    get_workspace_diff,
    read_workspace_file,
    write_workspace_file,
)


class WorkspaceEngineerError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class WorkspaceEditResult:
    workspace_id: str
    path: str
    changed: bool
    diff: str


def read_file_range(
    *,
    workspace_id: str,
    path: str,
    start_line: int,
    end_line: int,
) -> str:
    if start_line < 1:
        raise WorkspaceEngineerError(
            "start_line must be at least 1."
        )

    if end_line < start_line:
        raise WorkspaceEngineerError(
            "end_line must be greater than or "
            "equal to start_line."
        )

    try:
        content = read_workspace_file(
            workspace_id=workspace_id,
            relative_path=path,
        )

    except WorkspaceError as exc:
        raise WorkspaceEngineerError(
            str(exc)
        ) from exc

    lines = content.splitlines()

    if start_line > len(lines):
        raise WorkspaceEngineerError(
            "start_line exceeds file length."
        )

    selected = lines[
        start_line - 1:end_line
    ]

    return "\n".join(
        f"{index}: {line}"
        for index, line in enumerate(
            selected,
            start=start_line,
        )
    )


def replace_file_lines(
    *,
    workspace_id: str,
    path: str,
    start_line: int,
    end_line: int,
    replacement_text: str,
) -> WorkspaceEditResult:
    if start_line < 1:
        raise WorkspaceEngineerError(
            "start_line must be at least 1."
        )

    if end_line < start_line:
        raise WorkspaceEngineerError(
            "end_line must be greater than or "
            "equal to start_line."
        )

    if not isinstance(
        replacement_text,
        str,
    ):
        raise WorkspaceEngineerError(
            "replacement_text must be a string."
        )

    try:
        original_content = read_workspace_file(
            workspace_id=workspace_id,
            relative_path=path,
        )

    except WorkspaceError as exc:
        raise WorkspaceEngineerError(
            str(exc)
        ) from exc

    lines = original_content.splitlines()

    if start_line > len(lines):
        raise WorkspaceEngineerError(
            "start_line exceeds file length."
        )

    if end_line > len(lines):
        raise WorkspaceEngineerError(
            "end_line exceeds file length."
        )

    replacement_lines = (
        replacement_text.splitlines()
    )

    new_lines = (
        lines[:start_line - 1]
        + replacement_lines
        + lines[end_line:]
    )

    trailing_newline = (
        "\n"
        if original_content.endswith("\n")
        else ""
    )

    new_content = (
        "\n".join(
            new_lines
        )
        + trailing_newline
    )

    if new_content == original_content:
        raise WorkspaceEngineerError(
            "Workspace edit produced no changes."
        )

    try:
        write_workspace_file(
            workspace_id=workspace_id,
            relative_path=path,
            content=new_content,
        )

        diff = get_workspace_diff(
            workspace_id
        )

    except WorkspaceError as exc:
        raise WorkspaceEngineerError(
            str(exc)
        ) from exc

    return WorkspaceEditResult(
        workspace_id=workspace_id,
        path=path,
        changed=True,
        diff=diff,
    )
