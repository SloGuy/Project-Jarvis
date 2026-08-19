import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import subprocess
from pathlib import Path

from app.agents.workspaces import (
    WorkspaceError,
    get_workspace,
    get_workspace_diff,
    read_workspace_file,
    write_workspace_file,
)

from app.agents.patches import (
    CodePatch,
    create_patch,
)


OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://127.0.0.1:11434",
).rstrip("/")

OLLAMA_MODEL = os.getenv(
    "JARVIS_WORKSPACE_ENGINEER_MODEL",
    "qwen3:8b",
)

OLLAMA_TIMEOUT_SECONDS = int(
    os.getenv(
        "JARVIS_WORKSPACE_ENGINEER_TIMEOUT_SECONDS",
        "300",
    )
)

MAX_ENGINEERING_OBJECTIVE_LENGTH = 2000

MAX_SOURCE_CONTEXT_LENGTH = 30000

MAX_SOURCE_CONTEXT_LINES = 300

MAX_REPLACEMENT_LENGTH = 20000

MAX_LLM_REQUEST_ATTEMPTS = 2


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


@dataclass(frozen=True)
class WorkspaceSearchResult:
    workspace_id: str
    query: str
    matches: tuple[str, ...]


@dataclass(frozen=True)
class WorkspaceFileCandidate:
    path: str
    score: int
    matches: tuple[str, ...]


@dataclass(frozen=True)
class WorkspaceDiscoveryResult:
    workspace_id: str
    objective: str
    search_terms: tuple[str, ...]
    candidates: tuple[WorkspaceFileCandidate, ...]


@dataclass(frozen=True)
class WorkspaceTargetSelection:
    workspace_id: str
    objective: str
    path: str
    rationale: str
    model: str


@dataclass(frozen=True)
class WorkspaceVerificationResult:
    command: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str
    passed: bool


@dataclass(frozen=True)
class WorkspaceEditProposal:
    path: str
    start_line: int
    end_line: int
    replacement_text: str
    rationale: str
    model: str


ENGINEERING_SYSTEM_PROMPT = """
You are the Jarvis Workspace Software Engineer.

Your job is to propose one safe, bounded source-code edit
for a file inside an isolated Jarvis engineering workspace.

You are proposing an edit, not directly modifying the
filesystem.

Rules:

1. Return valid JSON only.

2. Modify only the file supplied in the request.

3. Return exactly one contiguous line-range replacement.

4. start_line and end_line must refer to lines visible in
   the supplied source context.

5. replacement_text must contain the complete replacement
   for that line range.

6. Preserve existing behavior unless the engineering
   objective explicitly requires changing it.

7. Make the smallest reasonable change that satisfies the
   objective.

8. Do not include Markdown fences.

9. Do not generate shell commands.

10. Do not bypass review, approval, or verification.

11. Do not claim the change has already been applied.

Return exactly this JSON structure:

{
    "path": "project/relative/path.py",
    "start_line": 1,
    "end_line": 1,
    "replacement_text": "replacement source code",
    "rationale": "short explanation of the proposed edit"
}
""".strip()


@dataclass(frozen=True)
class WorkspaceEngineeringRun:
    workspace_id: str
    proposal: WorkspaceEditProposal
    edit_result: WorkspaceEditResult
    verification: WorkspaceVerificationResult


@dataclass(frozen=True)
class WorkspacePatchRun:
    workspace_id: str
    engineering_run: WorkspaceEngineeringRun
    patch: CodePatch


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

    selected_text = "\n".join(
        lines[
            start_line - 1:end_line
        ]
    )

    replacement_joined = "\n".join(
        replacement_lines
    )

    selected_brace_balance = (
        selected_text.count("{")
        - selected_text.count("}")
    )

    replacement_brace_balance = (
        replacement_joined.count("{")
        - replacement_joined.count("}")
    )

    if (
        selected_brace_balance > 0
        and replacement_brace_balance == 0
        and end_line < len(lines)
        and lines[end_line].strip() == "}"
    ):
        end_line += 1

    original_line = (
        lines[start_line - 1]
    )

    leading_whitespace = (
        original_line[
            :len(original_line)
            - len(original_line.lstrip())
        ]
    )

    if replacement_lines:
        first_nonempty = next(
            (
                line
                for line in replacement_lines
                if line.strip()
            ),
            None,
        )

        if first_nonempty is not None:
            replacement_indent = (
                first_nonempty[
                    :len(first_nonempty)
                    - len(first_nonempty.lstrip())
                ]
            )

            if (
                len(replacement_indent)
                < len(leading_whitespace)
            ):
                indentation_prefix = (
                    leading_whitespace[
                        len(replacement_indent):
                    ]
                )

                replacement_lines = [
                    (
                        indentation_prefix
                        + line
                        if line.strip()
                        else line
                    )
                    for line in replacement_lines
                ]

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


def _validate_engineering_objective(
    objective: Any,
) -> str:
    if not isinstance(
        objective,
        str,
    ):
        raise WorkspaceEngineerError(
            "Engineering objective must be a string."
        )

    normalized = objective.strip()

    if not normalized:
        raise WorkspaceEngineerError(
            "Engineering objective cannot be empty."
        )

    if (
        len(normalized)
        > MAX_ENGINEERING_OBJECTIVE_LENGTH
    ):
        raise WorkspaceEngineerError(
            "Engineering objective exceeds maximum "
            f"length of "
            f"{MAX_ENGINEERING_OBJECTIVE_LENGTH} "
            "characters."
        )

    return normalized


def _validate_edit_proposal(
    *,
    payload: Any,
    expected_path: str,
    source_line_count: int,
) -> tuple[
    int,
    int,
    str,
    str,
]:
    if not isinstance(
        payload,
        dict,
    ):
        raise WorkspaceEngineerError(
            "LLM edit proposal must be a JSON object."
        )

    proposed_path = payload.get(
        "path"
    )

    if proposed_path != expected_path:
        raise WorkspaceEngineerError(
            "LLM attempted to edit an unexpected path."
        )

    start_line = payload.get(
        "start_line"
    )

    end_line = payload.get(
        "end_line"
    )

    if (
        not isinstance(start_line, int)
        or isinstance(start_line, bool)
    ):
        raise WorkspaceEngineerError(
            "LLM start_line must be an integer."
        )

    if (
        not isinstance(end_line, int)
        or isinstance(end_line, bool)
    ):
        raise WorkspaceEngineerError(
            "LLM end_line must be an integer."
        )

    if start_line < 1:
        raise WorkspaceEngineerError(
            "LLM start_line must be at least 1."
        )

    if end_line < start_line:
        raise WorkspaceEngineerError(
            "LLM end_line cannot precede start_line."
        )

    if end_line > source_line_count:
        raise WorkspaceEngineerError(
            "LLM edit range exceeds source length."
        )

    replacement_text = payload.get(
        "replacement_text"
    )

    if not isinstance(
        replacement_text,
        str,
    ):
        raise WorkspaceEngineerError(
            "LLM replacement_text must be a string."
        )

    if (
        len(replacement_text)
        > MAX_REPLACEMENT_LENGTH
    ):
        raise WorkspaceEngineerError(
            "LLM replacement exceeds maximum length."
        )

    rationale = payload.get(
        "rationale"
    )

    if not isinstance(
        rationale,
        str,
    ):
        raise WorkspaceEngineerError(
            "LLM rationale must be a string."
        )

    rationale = rationale.strip()

    if not rationale:
        raise WorkspaceEngineerError(
            "LLM rationale cannot be empty."
        )

    return (
        start_line,
        end_line,
        replacement_text,
        rationale,
    )


def _extract_llm_message_content(
    response_data: dict[str, Any],
) -> str:
    message = response_data.get(
        "message"
    )

    if not isinstance(
        message,
        dict,
    ):
        raise WorkspaceEngineerError(
            "Ollama response did not contain "
            "a message object."
        )

    content = message.get(
        "content"
    )

    if not isinstance(
        content,
        str,
    ):
        raise WorkspaceEngineerError(
            "Ollama response did not contain "
            "message content."
        )

    content = content.strip()

    if not content:
        raise WorkspaceEngineerError(
            "Ollama returned empty workspace "
            "engineer content."
        )

    return content


def propose_llm_workspace_edit(
    *,
    workspace_id: str,
    path: str,
    objective: str,
) -> WorkspaceEditProposal:
    normalized_objective = (
        _validate_engineering_objective(
            objective
        )
    )

    try:
        source_content = read_workspace_file(
            workspace_id=workspace_id,
            relative_path=path,
        )

    except WorkspaceError as exc:
        raise WorkspaceEngineerError(
            str(exc)
        ) from exc

    if not source_content.strip():
        raise WorkspaceEngineerError(
            "Workspace source file is empty."
        )

    source_lines = (
        source_content.splitlines()
    )

    context_start_line = 1
    context_end_line = len(
        source_lines
    )

    if (
        len(source_content)
        > MAX_SOURCE_CONTEXT_LENGTH
    ):
        objective_terms = {
            term
            for term in re.findall(
                r"[A-Za-z_][A-Za-z0-9_-]{2,}",
                normalized_objective.lower(),
            )
            if term
            not in {
                "the",
                "and",
                "that",
                "this",
                "with",
                "from",
                "into",
                "file",
                "change",
                "modify",
                "improve",
                "existing",
                "only",
            }
        }

        best_line_index = 0
        best_score = 0

        for index, line in enumerate(
            source_lines
        ):
            normalized_line = (
                line.lower()
            )

            score = sum(
                1
                for term in objective_terms
                if term in normalized_line
            )

            if score > best_score:
                best_score = score
                best_line_index = index

        half_window = (
            MAX_SOURCE_CONTEXT_LINES
            // 2
        )

        start_index = max(
            0,
            best_line_index
            - half_window,
        )

        end_index = min(
            len(source_lines),
            start_index
            + MAX_SOURCE_CONTEXT_LINES,
        )

        if (
            end_index
            - start_index
            < MAX_SOURCE_CONTEXT_LINES
        ):
            start_index = max(
                0,
                end_index
                - MAX_SOURCE_CONTEXT_LINES,
            )

        context_start_line = (
            start_index + 1
        )

        context_end_line = (
            end_index
        )

        context_lines = source_lines[
            start_index:end_index
        ]

    else:
        context_lines = source_lines

    numbered_source = "\n".join(
        f"{index}: {line}"
        for index, line in enumerate(
            context_lines,
            start=context_start_line,
        )
    )

    user_prompt = (
        "Engineering objective:\n"
        f"{normalized_objective}\n\n"
        "Target path:\n"
        f"{path}\n\n"
        "Current source code with line numbers:\n"
        f"{numbered_source}"
    )

    request_payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": "json",
        "messages": [
            {
                "role": "system",
                "content": (
                    ENGINEERING_SYSTEM_PROMPT
                ),
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        "options": {
            "temperature": 0.1,
        },
    }

    request = Request(
        (
            f"{OLLAMA_BASE_URL}"
            "/api/chat"
        ),
        data=json.dumps(
            request_payload
        ).encode(
            "utf-8"
        ),
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    response_data = None
    last_error = None

    for attempt in range(
        1,
        MAX_LLM_REQUEST_ATTEMPTS + 1,
    ):
        try:
            with urlopen(
                request,
                timeout=OLLAMA_TIMEOUT_SECONDS,
            ) as response:
                response_data = json.loads(
                    response.read().decode(
                        "utf-8"
                    )
                )

            break

        except HTTPError as exc:
            raise WorkspaceEngineerError(
                "Ollama workspace engineer request "
                f"failed with HTTP {exc.code}."
            ) from exc

        except (
            URLError,
            TimeoutError,
        ) as exc:
            last_error = exc

            if (
                attempt
                >= MAX_LLM_REQUEST_ATTEMPTS
            ):
                break

        except json.JSONDecodeError as exc:
            raise WorkspaceEngineerError(
                "Ollama returned invalid response JSON."
            ) from exc

    if response_data is None:
        if isinstance(
            last_error,
            TimeoutError,
        ):
            raise WorkspaceEngineerError(
                "Ollama workspace engineer request "
                "timed out after "
                f"{MAX_LLM_REQUEST_ATTEMPTS} attempts."
            ) from last_error

        raise WorkspaceEngineerError(
            "Unable to reach Ollama workspace "
            "engineer service after "
            f"{MAX_LLM_REQUEST_ATTEMPTS} attempts."
        ) from last_error

    content = _extract_llm_message_content(
        response_data
    )

    try:
        proposal_payload = json.loads(
            content
        )

    except json.JSONDecodeError as exc:
        raise WorkspaceEngineerError(
            "LLM returned invalid edit proposal JSON."
        ) from exc

    (
        start_line,
        end_line,
        replacement_text,
        rationale,
    ) = _validate_edit_proposal(
        payload=proposal_payload,
        expected_path=path,
        source_line_count=len(
            source_lines
        ),
    )

    if (
        start_line < context_start_line
        or end_line > context_end_line
    ):
        raise WorkspaceEngineerError(
            "LLM proposed an edit outside "
            "the visible source context."
        )

    start_source_line = (
        source_lines[start_line - 1]
    )

    if "{" in start_source_line:
        brace_balance = 0
        matching_end_line = None

        for line_number in range(
            start_line,
            context_end_line + 1,
        ):
            line = source_lines[
                line_number - 1
            ]

            brace_balance += (
                line.count("{")
                - line.count("}")
            )

            if brace_balance == 0:
                matching_end_line = (
                    line_number
                )
                break

        if matching_end_line is None:
            raise WorkspaceEngineerError(
                "Unable to resolve complete "
                "brace-delimited edit block "
                "inside visible source context."
            )

        if end_line < matching_end_line:
            end_line = (
                matching_end_line
            )

    return WorkspaceEditProposal(
        path=path,
        start_line=start_line,
        end_line=end_line,
        replacement_text=replacement_text,
        rationale=rationale,
        model=OLLAMA_MODEL,
    )


def apply_workspace_edit_proposal(
    *,
    workspace_id: str,
    proposal: WorkspaceEditProposal,
) -> WorkspaceEditResult:
    return replace_file_lines(
        workspace_id=workspace_id,
        path=proposal.path,
        start_line=proposal.start_line,
        end_line=proposal.end_line,
        replacement_text=proposal.replacement_text,
    )


def verify_workspace_file(
    *,
    workspace_id: str,
    path: str,
) -> WorkspaceVerificationResult:
    try:
        workspace = get_workspace(
            workspace_id
        )

    except WorkspaceError as exc:
        raise WorkspaceEngineerError(
            str(exc)
        ) from exc

    workspace_root = Path(
        workspace.path
    )

    target_path = (
        workspace_root
        / path
    ).resolve()

    try:
        target_path.relative_to(
            workspace_root.resolve()
        )

    except ValueError as exc:
        raise WorkspaceEngineerError(
            "Verification path escapes workspace."
        ) from exc

    if not target_path.is_file():
        raise WorkspaceEngineerError(
            f"Workspace file not found: {path}"
        )

    suffix = (
        target_path.suffix.lower()
    )

    if suffix == ".py":
        command = (
            "python",
            "-m",
            "py_compile",
            path,
        )

        result = subprocess.run(
            command,
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False,
        )

        return WorkspaceVerificationResult(
            command=command,
            return_code=result.returncode,
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            passed=(
                result.returncode == 0
            ),
        )

    if suffix == ".json":
        command = (
            "python",
            "-m",
            "json.tool",
            path,
        )

        result = subprocess.run(
            command,
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False,
        )

        return WorkspaceVerificationResult(
            command=command,
            return_code=result.returncode,
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            passed=(
                result.returncode == 0
            ),
        )

    text_extensions = {
        ".html",
        ".css",
        ".js",
        ".md",
        ".txt",
    }

    if suffix in text_extensions:
        try:
            content = target_path.read_text(
                encoding="utf-8"
            )

        except (
            OSError,
            UnicodeError,
        ) as exc:
            return WorkspaceVerificationResult(
                command=(
                    "read_text",
                    path,
                ),
                return_code=1,
                stdout="",
                stderr=str(exc),
                passed=False,
            )

        if not content.strip():
            return WorkspaceVerificationResult(
                command=(
                    "read_text",
                    path,
                ),
                return_code=1,
                stdout="",
                stderr=(
                    "Verified text file is empty."
                ),
                passed=False,
            )

        return WorkspaceVerificationResult(
            command=(
                "read_text",
                path,
            ),
            return_code=0,
            stdout=(
                "Target exists and is readable "
                "as non-empty UTF-8 text."
            ),
            stderr="",
            passed=True,
        )

    raise WorkspaceEngineerError(
        "Unsupported workspace verification "
        f"type: {suffix or '<no extension>'}"
    )


def execute_llm_workspace_edit(
    *,
    workspace_id: str,
    path: str,
    objective: str,
) -> WorkspaceEngineeringRun:
    proposal = propose_llm_workspace_edit(
        workspace_id=workspace_id,
        path=path,
        objective=objective,
    )

    edit_result = apply_workspace_edit_proposal(
        workspace_id=workspace_id,
        proposal=proposal,
    )

    verification = verify_workspace_file(
        workspace_id=workspace_id,
        path=proposal.path,
    )

    if not verification.passed:
        raise WorkspaceEngineerError(
            "LLM workspace edit failed verification. "
            f"return_code={verification.return_code} "
            f"stderr={verification.stderr}"
        )

    return WorkspaceEngineeringRun(
        workspace_id=workspace_id,
        proposal=proposal,
        edit_result=edit_result,
        verification=verification,
    )


def create_workspace_patch_from_run(
    *,
    workspace_id: str,
    task_id: str,
    agent_id: str,
    engineering_run: WorkspaceEngineeringRun,
) -> WorkspacePatchRun:
    path = (
        engineering_run.proposal.path
    )

    proposed_content = read_workspace_file(
        workspace_id=workspace_id,
        relative_path=path,
    )

    if not proposed_content.strip():
        raise WorkspaceEngineerError(
            "Verified workspace edit produced "
            "empty proposed content."
        )

    try:
        patch = create_patch(
            task_id=task_id,
            agent_id=agent_id,
            path=path,
            proposed_content=proposed_content,
            description=(
                engineering_run
                .proposal
                .rationale
            ),
        )

    except ValueError as exc:
        raise WorkspaceEngineerError(
            "Unable to convert verified workspace "
            f"edit into CodePatch: {exc}"
        ) from exc

    return WorkspacePatchRun(
        workspace_id=workspace_id,
        engineering_run=engineering_run,
        patch=patch,
    )


def create_llm_workspace_patch(
    *,
    workspace_id: str,
    task_id: str,
    agent_id: str,
    path: str,
    objective: str,
) -> WorkspacePatchRun:
    engineering_run = (
        execute_llm_workspace_edit(
            workspace_id=workspace_id,
            path=path,
            objective=objective,
        )
    )

    return create_workspace_patch_from_run(
        workspace_id=workspace_id,
        task_id=task_id,
        agent_id=agent_id,
        engineering_run=engineering_run,
    )


def search_workspace_code(
    *,
    workspace_id: str,
    query: str,
) -> WorkspaceSearchResult:
    normalized_query = query.strip()

    if not normalized_query:
        raise WorkspaceEngineerError(
            "Workspace search query is required."
        )

    try:
        workspace = get_workspace(
            workspace_id
        )

    except WorkspaceError as exc:
        raise WorkspaceEngineerError(
            str(exc)
        ) from exc

    workspace_root = Path(
        workspace.path
    )

    result = subprocess.run(
        [
            "grep",
            "-Rni",
            "--exclude=*.pyc",
            "--exclude-dir=.git",
            "--exclude-dir=.venv",
            "--",
            normalized_query,
            "app",
        ],
        cwd=workspace_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    if result.returncode not in {
        0,
        1,
    }:
        raise WorkspaceEngineerError(
            "Workspace search failed: "
            f"{result.stderr.strip()}"
        )

    matches = tuple(
        line
        for line in result.stdout.splitlines()
        if line.strip()
    )

    return WorkspaceSearchResult(
        workspace_id=workspace_id,
        query=normalized_query,
        matches=matches,
    )


def discover_workspace_files(
    *,
    workspace_id: str,
    objective: str,
    max_candidates: int = 10,
) -> WorkspaceDiscoveryResult:
    normalized_objective = (
        _validate_engineering_objective(
            objective
        )
    )

    if max_candidates < 1:
        raise WorkspaceEngineerError(
            "max_candidates must be at least 1."
        )

    ignored_terms = {
        "the",
        "and",
        "that",
        "this",
        "with",
        "from",
        "into",
        "for",
        "file",
        "files",
        "change",
        "modify",
        "improve",
        "update",
        "fix",
        "make",
        "should",
        "only",
        "existing",
        "jarvis",
    }

    terms = []

    for term in re.findall(
        r"[A-Za-z_][A-Za-z0-9_-]{2,}",
        normalized_objective.lower(),
    ):
        if term in ignored_terms:
            continue

        if term not in terms:
            terms.append(term)

    if not terms:
        raise WorkspaceEngineerError(
            "Engineering objective did not produce "
            "usable workspace search terms."
        )

    candidate_matches: dict[
        str,
        list[str],
    ] = {}

    candidate_scores: dict[
        str,
        int,
    ] = {}

    for term in terms:
        result = search_workspace_code(
            workspace_id=workspace_id,
            query=term,
        )

        matched_paths = set()

        for match in result.matches:
            path = match.split(
                ":",
                1,
            )[0]

            if not path.startswith(
                "app/"
            ):
                continue

            candidate_matches.setdefault(
                path,
                [],
            ).append(
                match
            )

            matched_paths.add(
                path
            )

        for path in matched_paths:
            candidate_scores[path] = (
                candidate_scores.get(
                    path,
                    0,
                )
                + 1
            )

    ranked = sorted(
        candidate_scores.items(),
        key=lambda item: (
            -item[1],
            item[0],
        ),
    )

    candidates = tuple(
        WorkspaceFileCandidate(
            path=path,
            score=score,
            matches=tuple(
                candidate_matches.get(
                    path,
                    [],
                )
            ),
        )
        for path, score in ranked[
            :max_candidates
        ]
    )

    return WorkspaceDiscoveryResult(
        workspace_id=workspace_id,
        objective=normalized_objective,
        search_terms=tuple(
            terms
        ),
        candidates=candidates,
    )


def select_workspace_target(
    *,
    discovery: WorkspaceDiscoveryResult,
) -> WorkspaceTargetSelection:
    if not discovery.candidates:
        raise WorkspaceEngineerError(
            "Workspace discovery produced no candidates."
        )

    candidate_context = []

    for candidate in discovery.candidates:
        candidate_context.append(
            {
                "path": candidate.path,
                "score": candidate.score,
                "matches": list(
                    candidate.matches[:8]
                ),
            }
        )

    system_prompt = """
You are the Jarvis Workspace Engineering target selector.

Choose the single best source file for the engineering
objective from the supplied candidate list.

Rules:

1. Return valid JSON only.
2. Choose exactly one path.
3. The path must exactly match one supplied candidate path.
4. Prefer the file most directly responsible for the
   requested behavior or interface.
5. Do not choose a file merely because it contains many
   generic matching words.
6. Do not propose code changes.
7. Do not invent paths.

Return exactly:

{
    "path": "app/example.py",
    "rationale": "short explanation"
}
""".strip()

    request_payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": "json",
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": (
                    "Engineering objective:\n"
                    f"{discovery.objective}\n\n"
                    "Candidate files:\n"
                    + json.dumps(
                        candidate_context,
                        indent=2,
                    )
                ),
            },
        ],
        "options": {
            "temperature": 0.1,
        },
    }

    request = Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=json.dumps(
            request_payload
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(
            request,
            timeout=OLLAMA_TIMEOUT_SECONDS,
        ) as response:
            response_data = json.loads(
                response.read().decode(
                    "utf-8"
                )
            )

    except (
        HTTPError,
        URLError,
        TimeoutError,
        json.JSONDecodeError,
    ) as exc:
        raise WorkspaceEngineerError(
            "Workspace target selection failed."
        ) from exc

    content = _extract_llm_message_content(
        response_data
    )

    try:
        payload = json.loads(
            content
        )

    except json.JSONDecodeError as exc:
        raise WorkspaceEngineerError(
            "LLM returned invalid target selection JSON."
        ) from exc

    selected_path = payload.get(
        "path"
    )

    rationale = payload.get(
        "rationale"
    )

    allowed_paths = {
        candidate.path
        for candidate in discovery.candidates
    }

    if selected_path not in allowed_paths:
        raise WorkspaceEngineerError(
            "LLM selected a path outside "
            "the discovered candidate set."
        )

    if not isinstance(
        rationale,
        str,
    ) or not rationale.strip():
        raise WorkspaceEngineerError(
            "LLM target rationale is required."
        )

    return WorkspaceTargetSelection(
        workspace_id=discovery.workspace_id,
        objective=discovery.objective,
        path=selected_path,
        rationale=rationale.strip(),
        model=OLLAMA_MODEL,
    )


@dataclass(frozen=True)
class DiscoveredWorkspaceEngineeringRun:
    workspace_id: str
    discovery: WorkspaceDiscoveryResult
    selection: WorkspaceTargetSelection
    engineering_run: WorkspaceEngineeringRun


def execute_discovered_workspace_edit(
    *,
    workspace_id: str,
    objective: str,
) -> DiscoveredWorkspaceEngineeringRun:
    discovery = discover_workspace_files(
        workspace_id=workspace_id,
        objective=objective,
    )

    selection = select_workspace_target(
        discovery=discovery
    )

    engineering_run = (
        execute_llm_workspace_edit(
            workspace_id=workspace_id,
            path=selection.path,
            objective=objective,
        )
    )

    return DiscoveredWorkspaceEngineeringRun(
        workspace_id=workspace_id,
        discovery=discovery,
        selection=selection,
        engineering_run=engineering_run,
    )


def create_discovered_workspace_patch(
    *,
    workspace_id: str,
    task_id: str,
    agent_id: str,
    objective: str,
) -> WorkspacePatchRun:
    discovered_run = (
        execute_discovered_workspace_edit(
            workspace_id=workspace_id,
            objective=objective,
        )
    )

    return create_workspace_patch_from_run(
        workspace_id=workspace_id,
        task_id=task_id,
        agent_id=agent_id,
        engineering_run=(
            discovered_run.engineering_run
        ),
    )
