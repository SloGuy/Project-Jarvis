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
) -> ResearchCandidate:
    parent_id = _required_text(parent_research_id, "parent_research_id")
    name = _required_text(strategy_name, "strategy_name")
    name = name.lower().replace(" ", "_")
    thesis = _required_text(hypothesis, "hypothesis")
    reason = _required_text(revision_reason, "revision_reason")

    with locked_research_state(write=True) as state:
        rows = state["candidates"]
        if parent_id not in rows:
            raise KeyError(f"Unknown research candidate: {parent_id}")

        parent = ResearchCandidate.from_dict(rows[parent_id])
        if parent.status not in {
            ResearchStatus.REVISION_REQUIRED,
            ResearchStatus.REJECTED,
        }:
            raise ValueError("Parent must require revision or be rejected.")
        if name == parent.strategy_name:
            raise ValueError("Use a new strategy name for the revision.")
        if thesis == parent.hypothesis.strip():
            raise ValueError("The revised hypothesis must change.")
        if parent.hypothesis_version < 1:
            raise ValueError("Parent hypothesis version must be positive.")

        for row in rows.values():
            existing = ResearchCandidate.from_dict(row)
            if (
                existing.strategy_name == name
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
            status=ResearchStatus.PROPOSED,
            verdict=ResearchVerdict.PENDING,
            created_at=now,
            updated_at=now,
            evidence=[],
            concerns=[],
            evaluation_notes=None,
            reviewed_at=None,
            review_history=[],
            asset_universe=list(parent.asset_universe),
            data_requirements=list(parent.data_requirements),
            success_criteria=list(parent.success_criteria),
        )
        rows[child.research_id] = child.to_dict()

    return child
