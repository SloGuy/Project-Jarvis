import difflib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.agents.patches import (
    CodePatch,
    create_patch,
)


PROJECT_ROOT = Path(
    __file__
).resolve().parents[2]

OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://127.0.0.1:11434",
).rstrip("/")

OLLAMA_MODEL = os.getenv(
    "JARVIS_SOFTWARE_ENGINEER_MODEL",
    "qwen3:8b",
)

OLLAMA_TIMEOUT_SECONDS = int(
    os.getenv(
        "JARVIS_SOFTWARE_ENGINEER_TIMEOUT_SECONDS",
        "180",
    )
)

MAX_SOURCE_CHARACTERS = 120000

MAX_OBJECTIVE_CHARACTERS = 4000

MAX_CHANGED_LINES = 250

MAX_PROPOSAL_ATTEMPTS = 2

SOFTWARE_ENGINEER_AGENT_ID = (
    "engineering.software_engineer"
)


class SoftwareEngineerError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class CodeProposal:
    path: str
    description: str
    proposed_content: str
    model: str


@dataclass(frozen=True)
class BoundedEdit:
    path: str
    description: str
    search_text: str
    replacement_text: str


SYSTEM_PROMPT = """
You are the Jarvis Software Engineer.

Your job is to propose one small, bounded edit to one existing
source file in the Jarvis repository in order to satisfy a
specific engineering objective.

You are proposing code only. You do not have authority to apply
the change.

Rules:

1. Return valid JSON only.

2. Modify only the exact target file supplied in the request.

3. Do not return the complete file.

4. Return one exact search_text string copied from the current
   file and one replacement_text string that should replace it.

5. search_text must occur exactly once in the current file.

6. Include enough surrounding text in search_text to make the
   match unique.

7. replacement_text must contain the complete replacement for
   search_text, including any unchanged surrounding context that
   should remain.

8. Make the smallest reasonable change needed to satisfy the
   objective.

9. Preserve existing functionality unless the objective requires
   changing it.

10. Do not claim the change has been applied.

11. Do not create approvals.

12. Do not bypass review.

13. Do not include markdown code fences.

14. Do not modify secrets, credentials, environment files, SSH
    configuration, systemd configuration, or files outside the
    Jarvis project.

Return exactly this JSON structure:

{
    "path": "exact supplied target path",
    "description": "short description of the proposed change",
    "search_text": "exact existing text to replace",
    "replacement_text": "complete replacement for search_text"
}
""".strip()


def _validate_target_path(
    path: str,
) -> tuple[str, Path]:
    if not isinstance(
        path,
        str,
    ):
        raise SoftwareEngineerError(
            "Target path must be a string."
        )

    normalized = path.strip()

    if not normalized:
        raise SoftwareEngineerError(
            "Target path cannot be empty."
        )

    requested = Path(
        normalized
    )

    if requested.is_absolute():
        raise SoftwareEngineerError(
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
    except ValueError as exc:
        raise SoftwareEngineerError(
            "Target path escapes the "
            "Jarvis project root."
        ) from exc

    if not resolved.exists():
        raise SoftwareEngineerError(
            f"Target path does not exist: "
            f"{normalized}"
        )

    if not resolved.is_file():
        raise SoftwareEngineerError(
            f"Target path is not a file: "
            f"{normalized}"
        )

    relative_path = str(
        resolved.relative_to(
            PROJECT_ROOT
        )
    )

    return (
        relative_path,
        resolved,
    )


def _read_source(
    path: Path,
) -> str:
    try:
        content = path.read_text(
            encoding="utf-8"
        )
    except (
        OSError,
        UnicodeError,
    ) as exc:
        raise SoftwareEngineerError(
            "Could not read target source file: "
            f"{exc}"
        ) from exc

    if len(content) > MAX_SOURCE_CHARACTERS:
        raise SoftwareEngineerError(
            "Target source file exceeds "
            "maximum planner size."
        )

    return content


def _extract_message_content(
    response_data: dict[str, Any],
) -> str:
    message = response_data.get(
        "message"
    )

    if not isinstance(
        message,
        dict,
    ):
        raise SoftwareEngineerError(
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
        raise SoftwareEngineerError(
            "Ollama response did not contain "
            "message content."
        )

    content = content.strip()

    if not content:
        raise SoftwareEngineerError(
            "Ollama returned empty software "
            "engineer content."
        )

    return content


def _validate_proposal(
    *,
    target_path: str,
    payload: Any,
) -> BoundedEdit:
    if not isinstance(
        payload,
        dict,
    ):
        raise SoftwareEngineerError(
            "Software Engineer output must "
            "be a JSON object."
        )

    llm_error = payload.get(
        "error"
    )

    if isinstance(
        llm_error,
        str,
    ) and llm_error.strip():
        raise SoftwareEngineerError(
            "Software Engineer returned an error: "
            f"{llm_error.strip()}"
        )

    proposed_path = payload.get(
        "path"
    )

    if proposed_path is None:
        proposed_path = target_path

    elif not isinstance(
        proposed_path,
        str,
    ):
        raise SoftwareEngineerError(
            "Proposal path must be a string."
        )

    else:
        proposed_path = (
            proposed_path.strip()
        )

        if not proposed_path:
            proposed_path = target_path

    if proposed_path != target_path:
        raise SoftwareEngineerError(
            "Software Engineer attempted to "
            "change a different file: "
            f"{proposed_path}"
        )

    description = payload.get(
        "description"
    )

    if not isinstance(
        description,
        str,
    ) or not description.strip():
        description = (
            "Software Engineer proposal for "
            f"{target_path}."
        )

    else:
        description = (
            description.strip()
        )

    search_text = payload.get(
        "search_text"
    )

    if not isinstance(
        search_text,
        str,
    ):
        raise SoftwareEngineerError(
            "search_text must be a string."
        )

    if not search_text:
        raise SoftwareEngineerError(
            "search_text cannot be empty."
        )

    if "... omitted ..." in search_text:
        raise SoftwareEngineerError(
            "Software Engineer search_text "
            "cannot contain omitted-context markers."
        )

    replacement_text = payload.get(
        "replacement_text"
    )

    if not isinstance(
        replacement_text,
        str,
    ):
        raise SoftwareEngineerError(
            "replacement_text must be a string."
        )

    if search_text == replacement_text:
        raise SoftwareEngineerError(
            "Software Engineer proposed "
            "no source changes."
        )

    return BoundedEdit(
        path=target_path,
        description=description,
        search_text=search_text,
        replacement_text=replacement_text,
    )


def _apply_bounded_edit(
    *,
    source_content: str,
    edit: BoundedEdit,
) -> str:
    occurrence_count = (
        source_content.count(
            edit.search_text
        )
    )

    if occurrence_count == 0:
        raise SoftwareEngineerError(
            "Software Engineer search_text "
            "was not found in the target file."
        )

    if occurrence_count != 1:
        raise SoftwareEngineerError(
            "Software Engineer search_text "
            "must match exactly once. "
            f"Found {occurrence_count} matches."
        )

    proposed_content = (
        source_content.replace(
            edit.search_text,
            edit.replacement_text,
            1,
        )
    )

    if proposed_content == source_content:
        raise SoftwareEngineerError(
            "Software Engineer proposed "
            "no source changes."
        )

    if (
        len(proposed_content)
        > MAX_SOURCE_CHARACTERS
    ):
        raise SoftwareEngineerError(
            "Proposed source exceeds "
            "maximum size."
        )

    return proposed_content


def _validate_change_scope(
    *,
    original_content: str,
    proposed_content: str,
) -> None:
    diff_lines = list(
        difflib.unified_diff(
            original_content.splitlines(),
            proposed_content.splitlines(),
        )
    )

    changed_lines = sum(
        1
        for line in diff_lines
        if (
            line.startswith("+")
            or line.startswith("-")
        )
        and not line.startswith("+++")
        and not line.startswith("---")
    )

    if changed_lines == 0:
        raise SoftwareEngineerError(
            "Software Engineer proposed "
            "no source changes."
        )

    if changed_lines > MAX_CHANGED_LINES:
        raise SoftwareEngineerError(
            "Software Engineer proposal exceeds "
            "maximum change scope: "
            f"{changed_lines} changed lines > "
            f"{MAX_CHANGED_LINES}."
        )


def _build_source_context(
    *,
    source_content: str,
    objective: str,
    maximum_lines: int = 180,
) -> str:
    lines = source_content.splitlines()

    if len(lines) <= maximum_lines:
        return source_content

    objective_words = {
        word.strip(
            ".,:;()[]{}<>\"'`"
        ).lower()
        for word in objective.split()
        if len(
            word.strip(
                ".,:;()[]{}<>\"'`"
            )
        ) >= 4
    }

    best_index = 0
    best_score = -1

    for index, line in enumerate(lines):
        normalized = line.lower()

        score = sum(
            1
            for word in objective_words
            if word in normalized
        )

        if score > best_score:
            best_score = score
            best_index = index

    half_window = maximum_lines // 2

    start = max(
        0,
        best_index - half_window,
    )

    end = min(
        len(lines),
        start + maximum_lines,
    )

    if end - start < maximum_lines:
        start = max(
            0,
            end - maximum_lines,
        )

    return "\n".join(
        lines[start:end]
    )


def propose_file_change(
    *,
    objective: str,
    path: str,
) -> CodeProposal:
    if not isinstance(
        objective,
        str,
    ):
        raise SoftwareEngineerError(
            "Objective must be a string."
        )

    normalized_objective = (
        objective.strip()
    )

    if not normalized_objective:
        raise SoftwareEngineerError(
            "Objective cannot be empty."
        )

    if (
        len(normalized_objective)
        > MAX_OBJECTIVE_CHARACTERS
    ):
        raise SoftwareEngineerError(
            "Objective exceeds maximum length."
        )

    (
        relative_path,
        resolved_path,
    ) = _validate_target_path(
        path
    )

    source_content = _read_source(
        resolved_path
    )

    source_context = (
        _build_source_context(
            source_content=source_content,
            objective=(
                normalized_objective
            ),
        )
    )

    request_context = {
        "objective": (
            normalized_objective
        ),
        "target_path": (
            relative_path
        ),
        "source_excerpt": (
            source_context
        ),
        "instructions": (
            "Use only exact text that appears in "
            "one contiguous section of source_excerpt "
            "for search_text. Never include the "
            "\"... omitted ...\" marker in search_text. "
            "Return one small bounded edit."
        ),
    }

    request_body = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": "json",
        "think": False,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": json.dumps(
                    request_context,
                    indent=2,
                ),
            },
        ],
        "options": {
            "temperature": 0.1,
            "num_predict": 2000,
        },
    }

    request = Request(
        url=(
            f"{OLLAMA_BASE_URL}/api/chat"
        ),
        data=json.dumps(
            request_body
        ).encode(
            "utf-8"
        ),
        headers={
            "Content-Type": (
                "application/json"
            ),
        },
        method="POST",
    )

    try:
        with urlopen(
            request,
            timeout=(
                OLLAMA_TIMEOUT_SECONDS
            ),
        ) as response:
            response_data = json.loads(
                response.read().decode(
                    "utf-8"
                )
            )

    except HTTPError as exc:
        error_body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise SoftwareEngineerError(
            f"Ollama returned HTTP "
            f"{exc.code}: {error_body}"
        ) from exc

    except URLError as exc:
        raise SoftwareEngineerError(
            "Could not connect to Ollama: "
            f"{exc.reason}"
        ) from exc

    except TimeoutError as exc:
        raise SoftwareEngineerError(
            "Software Engineer generation "
            "timed out."
        ) from exc

    except json.JSONDecodeError as exc:
        raise SoftwareEngineerError(
            "Ollama returned invalid "
            "response JSON."
        ) from exc

    message_content = (
        _extract_message_content(
            response_data
        )
    )

    try:
        payload = json.loads(
            message_content
        )

    except json.JSONDecodeError as exc:
        raise SoftwareEngineerError(
            "Ollama did not return a valid "
            "JSON code proposal."
        ) from exc


    edit = _validate_proposal(
        target_path=relative_path,
        payload=payload,
    )

    proposed_content = (
        _apply_bounded_edit(
            source_content=source_content,
            edit=edit,
        )
    )

    _validate_change_scope(
        original_content=source_content,
        proposed_content=(
            proposed_content
        ),
    )

    return CodeProposal(
        path=relative_path,
        description=(
            edit.description
        ),
        proposed_content=(
            proposed_content
        ),
        model=OLLAMA_MODEL,
    )


def create_proposed_patch(
    *,
    task_id: str,
    objective: str,
    path: str,
) -> CodePatch:
    if not isinstance(
        task_id,
        str,
    ) or not task_id.strip():
        raise SoftwareEngineerError(
            "task_id is required."
        )

    proposal = propose_file_change(
        objective=objective,
        path=path,
    )

    return create_patch(
        task_id=task_id.strip(),
        agent_id=(
            SOFTWARE_ENGINEER_AGENT_ID
        ),
        path=proposal.path,
        proposed_content=(
            proposal.proposed_content
        ),
        description=(
            proposal.description
        ),
    )
