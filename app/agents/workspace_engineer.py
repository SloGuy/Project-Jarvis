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
from app.agents.patch_sets import (
    create_patch_set,
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

SCOPE_SELECTOR_MODEL = os.getenv(
    "JARVIS_WORKSPACE_SCOPE_MODEL",
    "qwen3:8b",
)

SCOPE_SELECTOR_TIMEOUT_SECONDS = int(
    os.getenv(
        "JARVIS_WORKSPACE_SCOPE_TIMEOUT_SECONDS",
        "60",
    )
)

SCOPE_SELECTOR_MAX_ATTEMPTS = 1

MAX_ENGINEERING_OBJECTIVE_LENGTH = 2000

MAX_SOURCE_CONTEXT_LENGTH = 30000

MAX_SOURCE_CONTEXT_LINES = 300

MAX_REPLACEMENT_LENGTH = 20000

MAX_LLM_REQUEST_ATTEMPTS = 2

MAX_WORKSPACE_REPAIR_ATTEMPTS = 2

MAX_WORKSPACE_PROPOSAL_RETRIES = 1


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
class WorkspaceMultiTargetSelection:
    workspace_id: str
    objective: str
    paths: tuple[str, ...]
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

3. Return exactly one contiguous source replacement.

4. search_text must exactly copy complete existing lines
   visible in the supplied source context.

5. search_text must identify one unique source block.

6. replacement_text must contain the complete replacement
   for that exact source block.

6. Preserve existing behavior unless the engineering
   objective explicitly requires changing it.

7. Make the smallest reasonable change that satisfies the
   objective.

8. Do not include Markdown fences.

9. Do not generate shell commands.

10. Do not bypass review, approval, or verification.

11. Do not claim the change has already been applied.

Return exactly this JSON structure:

Return exactly this JSON structure:

{
    "search_text": "exact existing source lines",
    "replacement_text": "literal replacement source code",
    "rationale": "short explanation of the proposed edit"
}

replacement_text must contain literal source code only.
Do not summarize, explain, describe, or discuss the source file
inside replacement_text.
Do not include Markdown or prose in replacement_text.
""".strip()


EDIT_PROPOSAL_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "search_text": {
            "type": "string",
        },
        "replacement_text": {
            "type": "string",
        },
        "rationale": {
            "type": "string",
        },
    },
    "required": [
        "search_text",
        "replacement_text",
        "rationale",
    ],
    "additionalProperties": False,
}


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


@dataclass(frozen=True)
class MultiFileWorkspaceEngineeringRun:
    workspace_id: str
    engineering_runs: tuple[
        WorkspaceEngineeringRun,
        ...
    ]
    patch_set_id: str


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

    suffix = Path(
        path
    ).suffix.lower()

    start_line_text = (
        lines[start_line - 1]
        .strip()
    )

    is_simple_css_block = (
        suffix in {
            ".css",
            ".html",
        }
        and re.match(
            r"^[.#][^{]+\{\s*$",
            start_line_text,
        )
        and len(replacement_lines) >= 2
        and replacement_lines[
            0
        ].strip().endswith("{")
        and replacement_lines[
            -1
        ].strip() == "}"
    )

    if is_simple_css_block:
        base_indent = (
            leading_whitespace
        )

        body_indent = (
            base_indent
            + "    "
        )

        normalized_lines = []

        for index, line in enumerate(
            replacement_lines
        ):
            stripped = line.strip()

            if not stripped:
                normalized_lines.append(
                    ""
                )

            elif (
                index == 0
                or index
                == len(replacement_lines) - 1
            ):
                normalized_lines.append(
                    base_indent
                    + stripped
                )

            else:
                normalized_lines.append(
                    body_indent
                    + stripped
                )

        replacement_lines = (
            normalized_lines
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


def _validate_anchor_edit_proposal(
    *,
    payload: Any,
) -> tuple[str, str, str]:
    if not isinstance(payload, dict):
        raise WorkspaceEngineerError(
            "LLM edit proposal must be a JSON object."
        )

    search_text = payload.get("search_text")
    if not isinstance(search_text, str):
        raise WorkspaceEngineerError(
            "LLM search_text must be a string."
        )
    if not search_text.strip():
        raise WorkspaceEngineerError(
            "LLM search_text cannot be empty."
        )

    replacement_text = payload.get("replacement_text")
    if not isinstance(replacement_text, str):
        raise WorkspaceEngineerError(
            "LLM replacement_text must be a string."
        )

    rationale = payload.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise WorkspaceEngineerError(
            "LLM rationale must be a non-empty string."
        )

    return search_text, replacement_text, rationale.strip()


def _resolve_anchor_line_range(
    *,
    search_text: str,
    context_lines: list[str],
    context_start_line: int,
) -> tuple[int, int]:
    search_lines = search_text.splitlines()

    if not search_lines:
        raise WorkspaceEngineerError(
            "LLM search_text contains no source lines."
        )

    matches = []

    for index in range(
        len(context_lines) - len(search_lines) + 1
    ):
        candidate_lines = context_lines[
            index:index + len(search_lines)
        ]

        if candidate_lines == search_lines:
            matches.append(index)

    if not matches:
        normalized_search = [
            line.strip()
            for line in search_lines
        ]

        for index in range(
            len(context_lines) - len(search_lines) + 1
        ):
            candidate_lines = context_lines[
                index:index + len(search_lines)
            ]

            normalized_candidate = [
                line.strip()
                for line in candidate_lines
            ]

            if (
                normalized_candidate
                == normalized_search
            ):
                matches.append(index)

    if len(matches) != 1:
        raise WorkspaceEngineerError(
            "LLM search_text must match exactly once "
            f"in visible source context; matches={len(matches)}."
        )

    start_line = (
        context_start_line + matches[0]
    )

    end_line = (
        start_line + len(search_lines) - 1
    )

    return start_line, end_line


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


def _parse_llm_json_object(
    content: str,
) -> dict[str, Any]:
    candidates = [
        content.strip(),
    ]

    stripped = content.strip()

    if stripped.startswith("```"):
        lines = stripped.splitlines()

        if lines:
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        candidates.append(
            "\n".join(lines).strip()
        )

    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")

    if (
        first_brace != -1
        and last_brace != -1
        and last_brace > first_brace
    ):
        candidates.append(
            stripped[
                first_brace:last_brace + 1
            ]
        )

    for candidate in candidates:
        if not candidate:
            continue

        try:
            payload = json.loads(
                candidate
            )
        except json.JSONDecodeError:
            continue

        if isinstance(payload, dict):
            return payload

    raise WorkspaceEngineerError(
        "LLM returned invalid edit proposal JSON."
    )


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

        explicit_identifiers = {
            identifier
            for identifier in re.findall(
                r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?",
                normalized_objective,
            )
            if "_" in identifier or "." in identifier
        }

        best_line_index = 0
        best_score = 0

        for index, line in enumerate(
            source_lines
        ):
            normalized_line = (
                line.lower()
            )

            nearby_text = "\n".join(
                source_lines[
                    max(0, index - 20):
                    min(len(source_lines), index + 21)
                ]
            ).lower()

            score = sum(
                1
                for term in objective_terms
                if term in normalized_line
            )

            score += sum(
                2
                for term in objective_terms
                if term in nearby_text
            )

            score += sum(
                10
                for identifier in explicit_identifiers
                if identifier.lower() in nearby_text
            )

            if score > best_score:
                best_score = score
                best_line_index = index

        objective_lower = (
            normalized_objective.lower()
        )

        html_style_task = (
            Path(path).suffix.lower() == ".html"
            and any(
                term in objective_lower
                for term in (
                    "css",
                    "style",
                    "styling",
                    "presentation-only",
                )
            )
            and "do not modify javascript"
            in objective_lower
        )

        if html_style_task:
            style_end_index = next(
                (
                    index
                    for index, line
                    in enumerate(source_lines)
                    if "</style>" in line.lower()
                ),
                None,
            )

            if style_end_index is not None:
                best_line_index = (
                    style_end_index
                )

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
        "Edit safety rule:\n"
        f"{context_start_line}-{context_end_line}\n"
        "Your start_line and end_line MUST stay "
        "inside this range.\n\n"
        "Current source code with line numbers:\n"
        f"{numbered_source}"
    )

    request_payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "think": False,
        "format": EDIT_PROPOSAL_JSON_SCHEMA,
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
            "num_ctx": 8192,
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

    print(
        "WORKSPACE_ENGINEER_RAW_CONTENT:",
        repr(content),
        flush=True,
    )

    proposal_payload = (
        _parse_llm_json_object(
            content
        )
    )

    (
        search_text,
        replacement_text,
        rationale,
    ) = _validate_anchor_edit_proposal(
        payload=proposal_payload,
    )

    (
        start_line,
        end_line,
    ) = _resolve_anchor_line_range(
        search_text=search_text,
        context_lines=context_lines,
        context_start_line=context_start_line,
    )

    start_source_line = (
        source_lines[start_line - 1]
    )

    if (
        Path(path).suffix.lower()
        in {".css", ".html"}
        and re.match(
            r"^\s*[.#][^{]+\{\s*$",
            start_source_line,
        )
    ):
        brace_balance = 0
        matching_end_line = None

        brace_search_end_line = min(
            len(source_lines),
            context_end_line + 50,
        )

        for line_number in range(
            start_line,
            brace_search_end_line + 1,
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
                "brace-delimited edit block. "
                f"start_line={start_line} "
                f"brace_search_end_line="
                f"{brace_search_end_line} "
                f"source={start_source_line.strip()!r}"
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

    if suffix == ".html":
        content = target_path.read_text(
            encoding="utf-8"
        )

        style_blocks = list(
            re.finditer(
                r"<style\b[^>]*>(.*?)</style>",
                content,
                re.IGNORECASE | re.DOTALL,
            )
        )

        for style_match in style_blocks:
            css = style_match.group(1)

            if css.count("{") != css.count("}"):
                style_line = (
                    content[:style_match.start()].count("\n") + 1
                )

                return WorkspaceVerificationResult(
                    command=("html_css_braces", path),
                    return_code=1,
                    stdout="",
                    stderr=(
                        "Unbalanced CSS braces near line "
                        f"{style_line}."
                    ),
                    passed=False,
                )

        ids = re.findall(
            r"""\bid\s*=\s*["']([^"']+)["']""",
            content,
            re.IGNORECASE,
        )

        static_ids = [
            value
            for value in ids
            if "${" not in value
        ]

        duplicate_ids = sorted({
            value
            for value in static_ids
            if static_ids.count(value) > 1
        })

        if duplicate_ids:
            duplicate_id = duplicate_ids[0]
            matches = list(re.finditer(
                rf'''\bid\s*=\s*["']{re.escape(duplicate_id)}["']''',
                content,
                re.IGNORECASE,
            ))
            duplicate_line = (
                content[:matches[1].start()].count("\n") + 1
            )

            return WorkspaceVerificationResult(
                command=("html_duplicate_ids", path),
                return_code=1,
                stdout="",
                stderr=(
                    f"Duplicate HTML id at line "
                    f"{duplicate_line}: {duplicate_id}. "
                    "Duplicate HTML id values: "
                    + ", ".join(duplicate_ids)
                ),
                passed=False,
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


def _extract_verification_line(
    verification: WorkspaceVerificationResult,
) -> int | None:
    text = verification.stderr or verification.stdout
    match = re.search(r"line (\d+)", text)
    if not match:
        return None
    return int(match.group(1))


def _build_workspace_repair_objective(
    *,
    original_objective: str,
    verification: WorkspaceVerificationResult,
    repair_attempt: int,
) -> str:
    error_text = (
        verification.stderr
        or verification.stdout
        or "Verification failed."
    )
    target_line = _extract_verification_line(
        verification
    )

    return (
        "Repair the current source file so automated "
        "verification passes.\n"
        f"The failure is at line {target_line}.\n"
        "Your proposed edit MUST include that line.\n"
        "Do not repeat or redo the original edit.\n"
        "Preserve all unrelated existing changes.\n"
        f"Repair attempt: {repair_attempt}\n"
        f"Verification error:\n{error_text[:4000]}\n"
        "Original objective for context only:\n"
        f"{original_objective}"
    )


def execute_llm_workspace_edit(
    *,
    workspace_id: str,
    path: str,
    objective: str,
) -> WorkspaceEngineeringRun:
    current_objective = objective
    repair_attempt = 0
    last_verification = None
    repair_target_line = None
    proposal_retries = 0

    while True:
        try:
            proposal = propose_llm_workspace_edit(
                workspace_id=workspace_id,
                path=path,
                objective=current_objective,
            )
        except WorkspaceEngineerError as exc:
            error_text = str(exc)

            retryable_proposal_error = (
                "outside the visible source context"
                in error_text
                or "LLM returned invalid edit proposal JSON."
                in error_text
            )

            if not retryable_proposal_error:
                raise

            if (
                proposal_retries
                >= MAX_WORKSPACE_PROPOSAL_RETRIES
            ):
                raise

            proposal_retries += 1

            if (
                "LLM returned invalid edit proposal JSON."
                in error_text
            ):
                current_objective += (
                    "\n\nYour previous edit proposal was "
                    "invalid JSON. Return only a valid edit "
                    "proposal matching the required JSON "
                    "schema. Do not include markdown, code "
                    "fences, or commentary."
                )
            else:
                current_objective += (
                    "\n\nYour previous proposal was invalid. "
                    + error_text
                    + " Return a new proposal strictly inside "
                    "the visible line range."
                )

            continue

        proposal_retries = 0

        if (
            repair_target_line is not None
            and not (
                proposal.start_line
                <= repair_target_line
                <= proposal.end_line
            )
        ):
            if repair_attempt >= MAX_WORKSPACE_REPAIR_ATTEMPTS:
                raise WorkspaceEngineerError(
                    "Repair proposal missed required "
                    f"line {repair_target_line}."
                )
            repair_attempt += 1
            current_objective += (
                "\n\nYour repair must include line "
                f"{repair_target_line}."
            )
            continue

        try:
            edit_result = apply_workspace_edit_proposal(
                workspace_id=workspace_id,
                proposal=proposal,
            )
        except WorkspaceEngineerError as exc:
            if "produced no changes" not in str(exc):
                raise
            if last_verification is None:
                raise
            if repair_attempt >= MAX_WORKSPACE_REPAIR_ATTEMPTS:
                raise
            repair_attempt += 1
            current_objective = (
                _build_workspace_repair_objective(
                    original_objective=objective,
                    verification=last_verification,
                    repair_attempt=repair_attempt,
                )
                + "\n\nYour previous repair made no effective change."
            )
            continue

        verification = verify_workspace_file(
            workspace_id=workspace_id,
            path=proposal.path,
        )

        if verification.passed:
            return WorkspaceEngineeringRun(
                workspace_id=workspace_id,
                proposal=proposal,
                edit_result=edit_result,
                verification=verification,
            )

        if repair_attempt >= MAX_WORKSPACE_REPAIR_ATTEMPTS:
            raise WorkspaceEngineerError(
                "LLM workspace edit failed verification "
                "after repair attempts. "
                f"return_code={verification.return_code} "
                f"stderr={verification.stderr}"
            )

        last_verification = verification
        repair_target_line = (
            _extract_verification_line(
                verification
            )
        )
        repair_attempt += 1

        current_objective = _build_workspace_repair_objective(
            original_objective=objective,
            verification=verification,
            repair_attempt=repair_attempt,
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
            "-Rniw",
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
        "how",
        "are",
        "add",
        "data",
        "display",
        "displayed",
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

    for candidate in discovery.candidates[:6]:
        candidate_context.append(
            {
                "path": candidate.path,
                "score": candidate.score,
                "matches": [
                    match[:300]
                    for match
                    in candidate.matches[:3]
                ],
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
            "num_predict": 128,
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
                "Workspace multi-target selection "
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
                "Workspace multi-target selector "
                "returned invalid response JSON."
            ) from exc

    if response_data is None:
        if isinstance(
            last_error,
            TimeoutError,
        ):
            raise WorkspaceEngineerError(
                "Workspace multi-target selection "
                "timed out after "
                f"{MAX_LLM_REQUEST_ATTEMPTS} attempts."
            ) from last_error

        raise WorkspaceEngineerError(
            "Unable to reach workspace multi-target "
            "selection service after "
            f"{MAX_LLM_REQUEST_ATTEMPTS} attempts."
        ) from last_error

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
            "the discovered candidate set. "
            f"selected_path={selected_path!r} "
            f"allowed_paths={sorted(allowed_paths)!r}"
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


def create_workspace_patch_set_from_runs(
    *,
    workspace_id: str,
    task_id: str,
    agent_id: str,
    description: str,
    engineering_runs: tuple[
        WorkspaceEngineeringRun,
        ...
    ],
) -> MultiFileWorkspaceEngineeringRun:
    if not engineering_runs:
        raise WorkspaceEngineerError(
            "At least one engineering run is required."
        )

    patch_ids = []

    seen_paths = set()

    for engineering_run in engineering_runs:
        path = (
            engineering_run.proposal.path
        )

        if path in seen_paths:
            raise WorkspaceEngineerError(
                "Multi-file engineering run contains "
                f"duplicate path: {path}"
            )

        seen_paths.add(
            path
        )

        workspace_patch = (
            create_workspace_patch_from_run(
                workspace_id=workspace_id,
                task_id=task_id,
                agent_id=agent_id,
                engineering_run=(
                    engineering_run
                ),
            )
        )

        patch_ids.append(
            workspace_patch.patch.patch_id
        )

    try:
        patch_set = create_patch_set(
            task_id=task_id,
            agent_id=agent_id,
            description=description,
            patch_ids=tuple(
                patch_ids
            ),
        )

    except ValueError as exc:
        raise WorkspaceEngineerError(
            "Unable to create workspace patch set: "
            f"{exc}"
        ) from exc

    return MultiFileWorkspaceEngineeringRun(
        workspace_id=workspace_id,
        engineering_runs=engineering_runs,
        patch_set_id=(
            patch_set.patch_set_id
        ),
    )


def select_workspace_targets(
    *,
    discovery: WorkspaceDiscoveryResult,
    max_targets: int = 3,
) -> WorkspaceMultiTargetSelection:
    if not discovery.candidates:
        raise WorkspaceEngineerError(
            "Workspace discovery produced no candidates."
        )

    if max_targets < 1:
        raise WorkspaceEngineerError(
            "max_targets must be at least 1."
        )

    objective = (
        discovery.objective.lower()
    )

    scored_candidates = []

    for candidate in discovery.candidates:
        path = candidate.path.lower()

        score = candidate.score * 10

        infrastructure_terms = (
            "workspace",
            "workspace engineering",
            "patch",
            "patchset",
            "code editing",
            "source editing",
            "discovery",
            "scope selection",
        )

        if (
            path.endswith(
                "/workspace_engineer.py"
            )
            and not any(
                term in objective
                for term in infrastructure_terms
            )
        ):
            score -= 80

        # Frontend / UI responsibility.
        if any(
            term in objective
            for term in (
                "display",
                "ui",
                "interface",
                "page",
                "dashboard",
                "command center",
                "render",
                "show",
            )
        ):
            if (
                "/templates/" in path
                or path.endswith(".html")
                or path.endswith(".css")
                or path.endswith(".js")
            ):
                score += 40

        # Agent Command Center responsibility.
        if any(
            term in objective
            for term in (
                "agent command center",
                "command center",
                "agents command center",
            )
        ):
            if path.endswith(
                "/agent_command_center.html"
            ):
                score += 80

            elif (
                "/templates/" in path
                or path.endswith(".html")
            ):
                score -= 40

        # API responsibility.
        if any(
            term in objective
            for term in (
                "api",
                "endpoint",
                "expose",
                "route",
            )
        ):
            if (
                path.endswith(
                    "/api.py"
                )
                or path.endswith(
                    "api.py"
                )
            ):
                score += 60

        # Agent task/state responsibility.
        if any(
            term in objective
            for term in (
                "task",
                "tasks",
                "failure reason",
                "task status",
                "task error",
                "failed task",
            )
        ):
            if path.endswith(
                "/tasks.py"
            ):
                score += 60

        # Worker/execution responsibility.
        if any(
            term in objective
            for term in (
                "worker",
                "execute",
                "execution",
                "claim",
                "heartbeat",
                "recovery",
            )
        ):
            if path.endswith(
                "/worker.py"
            ):
                score += 45

        # Orchestration/review/approval responsibility.
        if any(
            term in objective
            for term in (
                "orchestrate",
                "orchestration",
                "review",
                "approval",
                "approve",
                "reject",
                "patch application",
            )
        ):
            if path.endswith(
                "/orchestrator.py"
            ):
                score += 45

        scored_candidates.append(
            (
                score,
                candidate.path,
            )
        )

    scored_candidates.sort(
        key=lambda item: (
            -item[0],
            item[1],
        )
    )

    if not scored_candidates:
        raise WorkspaceEngineerError(
            "Workspace scope selection "
            "produced no scored candidates."
        )

    selected = []

    def add_preferred_path(
        suffix: str,
    ) -> None:
        if len(selected) >= max_targets:
            return

        for _, candidate_path in scored_candidates:
            if candidate_path.lower().endswith(
                suffix
            ):
                if candidate_path not in selected:
                    selected.append(
                        candidate_path
                    )

                return

    # Explicit architectural responsibility should
    # outrank generic lexical similarity.
    if any(
        term in objective
        for term in (
            "agent command center",
            "command center",
            "agents command center",
        )
    ):
        add_preferred_path(
            "/agent_command_center.html"
        )

    if any(
        term in objective
        for term in (
            "api",
            "endpoint",
            "router",
            "route",
        )
    ):
        add_preferred_path(
            "/api.py"
        )

    # Fill any remaining capacity from the strongest
    # nearby discovery candidates.
    if len(selected) < max_targets:
        top_score = scored_candidates[0][0]

        for score, path in scored_candidates:
            if len(selected) >= max_targets:
                break

            if path in selected:
                continue

            if score < max(
                10,
                top_score - 30,
            ):
                continue

            selected.append(
                path
            )

    if not selected:
        selected = [
            scored_candidates[0][1]
        ]

    rationale = (
        "Deterministic workspace scope selection "
        "chose the smallest high-confidence file set "
        "using discovery relevance and architectural "
        "responsibility signals."
    )

    return WorkspaceMultiTargetSelection(
        workspace_id=discovery.workspace_id,
        objective=discovery.objective,
        paths=tuple(selected),
        rationale=rationale,
        model="deterministic-v1",
    )


def execute_discovered_multi_file_edit(
    *,
    workspace_id: str,
    task_id: str,
    agent_id: str,
    objective: str,
    max_targets: int = 3,
) -> MultiFileWorkspaceEngineeringRun:
    discovery = discover_workspace_files(
        workspace_id=workspace_id,
        objective=objective,
        max_candidates=20,
    )

    selection = select_workspace_targets(
        discovery=discovery,
        max_targets=max_targets,
    )

    if not selection.paths:
        raise WorkspaceEngineerError(
            "Multi-file workspace selection "
            "produced no target paths."
        )

    engineering_runs = []

    for path in selection.paths:
        engineering_run = (
            execute_llm_workspace_edit(
                workspace_id=workspace_id,
                path=path,
                objective=objective,
            )
        )

        engineering_runs.append(
            engineering_run
        )

    description = (
        "Discovered multi-file workspace edit: "
        f"{objective}"
    )

    return create_workspace_patch_set_from_runs(
        workspace_id=workspace_id,
        task_id=task_id,
        agent_id=agent_id,
        description=description,
        engineering_runs=tuple(
            engineering_runs
        ),
    )
