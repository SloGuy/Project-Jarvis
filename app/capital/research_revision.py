"""Research revisions with optional atomic request deduplication."""

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

from app.capital.research_models import (
    ResearchCandidate,
    ResearchStatus,
    ResearchVerdict,
)
from app.capital.research_service import (
    TERMINAL_RESEARCH_STATUSES,
    _required_text,
)
from app.capital.research_store import locked_research_state, utc_now_iso


def revise_research_candidate(
    *,
    parent_research_id: str,
    strategy_name: str,
    hypothesis: str,
    revision_reason: str,
    request_key: str | None = None,
    proposed_by: str | None = None,
) -> ResearchCandidate:
    parent_id = _required_text(parent_research_id, "parent_research_id")
    name = _required_text(strategy_name, "strategy_name")
    name = name.lower().replace(" ", "_")
    thesis = _required_text(hypothesis, "hypothesis")
    reason = _required_text(revision_reason, "revision_reason")

    if request_key is not None:
        if not isinstance(request_key, str):
            raise ValueError("request_key must be text.")
        request_key = _required_text(request_key, "request_key")
        if len(request_key) > 200:
            raise ValueError("request_key exceeds 200 characters.")

    if proposed_by is not None:
        if not isinstance(proposed_by, str):
            raise ValueError("proposed_by must be text.")
        proposed_by = _required_text(proposed_by, "proposed_by")

    request = {
        "parent_research_id": parent_id,
        "strategy_name": name,
        "hypothesis": thesis,
        "revision_reason": reason,
        "proposed_by": proposed_by,
    }

    with locked_research_state(write=True) as state:
        rows = state["candidates"]
        requests = state.get("revision_requests", {})
        if not isinstance(requests, dict):
            raise RuntimeError("Invalid revision request store.")

        if request_key is not None and request_key in requests:
            saved = requests[request_key]
            if saved["request"] != request:
                raise ValueError("Request key was used for another revision.")
            result = saved["result"]
            if result["research_id"] not in rows:
                raise RuntimeError("Previously created revision is missing.")
            # Return the original creation result even if later reviews
            # have changed the candidate. Do not reset its current state.
            return ResearchCandidate.from_dict(deepcopy(result))

        if parent_id not in rows:
            raise KeyError(f"Unknown research candidate: {parent_id}")

        parent = ResearchCandidate.from_dict(rows[parent_id])
        if parent.status not in {
            ResearchStatus.REVISION_REQUIRED,
            ResearchStatus.REJECTED,
        }:
            raise ValueError("Parent must require revision or be rejected.")
        if thesis == parent.hypothesis.strip():
            raise ValueError("The revised hypothesis must change.")
        if parent.hypothesis_version < 1:
            raise ValueError("Parent hypothesis version must be positive.")

        for row in rows.values():
            existing = ResearchCandidate.from_dict(row)
            if (
                existing.research_id != parent.research_id
                and existing.strategy_name == name
                and existing.status not in TERMINAL_RESEARCH_STATUSES
            ):
                raise ValueError(f"Active candidate already exists: {name}")

        now = utc_now_iso()
        child = replace(
            parent,
            research_id=f"research_{uuid4().hex}",
            strategy_name=name,
            display_name=f"{parent.display_name} — revision",
            hypothesis=thesis,
            hypothesis_version=parent.hypothesis_version + 1,
            parent_research_id=parent.research_id,
            revision_reason=reason,
            proposed_by=(
                parent.proposed_by if proposed_by is None else proposed_by
            ),
            status=ResearchStatus.PROPOSED,
            verdict=ResearchVerdict.PENDING,
            created_at=now,
            updated_at=now,
            evidence=[],
            concerns=[],
            evaluation_notes=None,
            reviewed_at=None,
            review_history=[],
            evaluation_attachments=[],
            validation_assessments=[],
            validation_recommendations=[],
            asset_universe=list(parent.asset_universe),
            data_requirements=list(parent.data_requirements),
            success_criteria=list(parent.success_criteria),
        )
        if (
            name == parent.strategy_name
            and parent.status == ResearchStatus.REVISION_REQUIRED
        ):
            parent.status = ResearchStatus.ARCHIVED
            parent.updated_at = now
            rows[parent.research_id] = parent.to_dict()

        result = child.to_dict()
        rows[child.research_id] = result

        if request_key is not None:
            requests[request_key] = {
                "request": deepcopy(request),
                "result": deepcopy(result),
            }
            state["revision_requests"] = requests

    return child
