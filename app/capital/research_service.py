import uuid
from typing import Any

from app.capital.research_models import (
    ResearchCandidate,
    ResearchStatus,
    ResearchVerdict,
)
from app.capital.research_store import (
    locked_research_state,
    utc_now_iso,
)


TERMINAL_RESEARCH_STATUSES = {
    ResearchStatus.REJECTED,
    ResearchStatus.ARCHIVED,
}


def _required_text(
    value: str,
    field_name: str,
) -> str:
    normalized = value.strip()

    if not normalized:
        raise ValueError(
            f"{field_name} must not be empty."
        )

    return normalized


def _text_list(
    values: list[str],
    field_name: str,
) -> list[str]:
    normalized = [
        value.strip()
        for value in values
        if value.strip()
    ]

    if not normalized:
        raise ValueError(
            f"{field_name} must contain "
            "at least one value."
        )

    return normalized


def propose_research_candidate(
    *,
    strategy_name: str,
    display_name: str,
    hypothesis: str,
    description: str,
    market_regime: str,
    asset_universe: list[str],
    data_requirements: list[str],
    risk_thesis: str,
    success_criteria: list[str],
    proposed_by: str,
) -> ResearchCandidate:
    normalized_strategy_name = (
        _required_text(
            strategy_name,
            "strategy_name",
        )
        .lower()
        .replace(" ", "_")
    )

    now = utc_now_iso()

    with locked_research_state(
        write=True
    ) as state:
        existing_candidates = [
            ResearchCandidate.from_dict(
                candidate_data
            )
            for candidate_data
            in state["candidates"].values()
        ]

        duplicate = next(
            (
                candidate
                for candidate
                in existing_candidates
                if (
                    candidate.strategy_name
                    == normalized_strategy_name
                    and candidate.status
                    not in TERMINAL_RESEARCH_STATUSES
                )
            ),
            None,
        )

        if duplicate is not None:
            raise ValueError(
                "An active research candidate "
                f"already exists for "
                f"{normalized_strategy_name}: "
                f"{duplicate.research_id}"
            )

        candidate = ResearchCandidate(
            research_id=(
                f"research_{uuid.uuid4().hex[:12]}"
            ),
            strategy_name=(
                normalized_strategy_name
            ),
            display_name=_required_text(
                display_name,
                "display_name",
            ),
            hypothesis=_required_text(
                hypothesis,
                "hypothesis",
            ),
            description=_required_text(
                description,
                "description",
            ),
            market_regime=_required_text(
                market_regime,
                "market_regime",
            ),
            asset_universe=_text_list(
                asset_universe,
                "asset_universe",
            ),
            data_requirements=_text_list(
                data_requirements,
                "data_requirements",
            ),
            risk_thesis=_required_text(
                risk_thesis,
                "risk_thesis",
            ),
            success_criteria=_text_list(
                success_criteria,
                "success_criteria",
            ),
            status=ResearchStatus.PROPOSED,
            verdict=ResearchVerdict.PENDING,
            proposed_by=_required_text(
                proposed_by,
                "proposed_by",
            ),
            created_at=now,
            updated_at=now,
        )

        state["candidates"][
            candidate.research_id
        ] = candidate.to_dict()

    return candidate


def list_research_candidates(
    *,
    status: ResearchStatus | None = None,
) -> list[ResearchCandidate]:
    with locked_research_state() as state:
        candidates = [
            ResearchCandidate.from_dict(
                candidate_data
            )
            for candidate_data
            in state["candidates"].values()
        ]

    if status is not None:
        candidates = [
            candidate
            for candidate in candidates
            if candidate.status == status
        ]

    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.created_at
        ),
        reverse=True,
    )


def get_research_candidate(
    *,
    research_id: str,
) -> ResearchCandidate | None:
    normalized_id = _required_text(
        research_id,
        "research_id",
    )

    with locked_research_state() as state:
        candidate_data = (
            state["candidates"].get(
                normalized_id
            )
        )

    if candidate_data is None:
        return None

    return ResearchCandidate.from_dict(
        candidate_data
    )


def require_research_candidate(
    *,
    research_id: str,
) -> ResearchCandidate:
    candidate = get_research_candidate(
        research_id=research_id,
    )

    if candidate is None:
        raise KeyError(
            "Unknown research candidate: "
            f"{research_id}"
        )

    return candidate


def get_research_summary() -> dict[str, Any]:
    candidates = list_research_candidates()

    counts = {
        status.value: sum(
            1
            for candidate in candidates
            if candidate.status == status
        )
        for status in ResearchStatus
    }

    return {
        "status": "success",
        "candidate_count": len(candidates),
        "status_counts": counts,
        "candidates": [
            candidate.to_dict()
            for candidate in candidates
        ],
    }
