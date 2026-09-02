from collections.abc import Iterable

from app.capital.research_models import (
    ResearchCandidate,
    ResearchStatus,
    ResearchVerdict,
)
from app.capital.research_store import (
    locked_research_state,
    utc_now_iso,
)


def _load_candidate(
    *,
    state: dict,
    research_id: str,
) -> ResearchCandidate:
    normalized_id = research_id.strip()

    if not normalized_id:
        raise ValueError(
            "research_id must not be empty."
        )

    candidate_data = (
        state["candidates"].get(
            normalized_id
        )
    )

    if candidate_data is None:
        raise KeyError(
            "Unknown research candidate: "
            f"{normalized_id}"
        )

    return ResearchCandidate.from_dict(
        candidate_data
    )


def _save_candidate(
    *,
    state: dict,
    candidate: ResearchCandidate,
) -> None:
    candidate.updated_at = utc_now_iso()

    state["candidates"][
        candidate.research_id
    ] = candidate.to_dict()


def _require_status(
    *,
    candidate: ResearchCandidate,
    allowed: set[ResearchStatus],
) -> None:
    if candidate.status not in allowed:
        allowed_values = ", ".join(
            sorted(
                status.value
                for status in allowed
            )
        )

        raise ValueError(
            f"{candidate.research_id} is "
            f"{candidate.status.value}; expected "
            f"one of: {allowed_values}."
        )


def _clean_items(
    values: Iterable[str],
) -> list[str]:
    return [
        value.strip()
        for value in values
        if value.strip()
    ]


def begin_research_screening(
    *,
    research_id: str,
) -> ResearchCandidate:
    with locked_research_state(
        write=True
    ) as state:
        candidate = _load_candidate(
            state=state,
            research_id=research_id,
        )

        _require_status(
            candidate=candidate,
            allowed={
                ResearchStatus.PROPOSED,
            },
        )

        candidate.status = (
            ResearchStatus.SCREENING
        )

        _save_candidate(
            state=state,
            candidate=candidate,
        )

    return candidate


def begin_strategy_research(
    *,
    research_id: str,
) -> ResearchCandidate:
    with locked_research_state(
        write=True
    ) as state:
        candidate = _load_candidate(
            state=state,
            research_id=research_id,
        )

        _require_status(
            candidate=candidate,
            allowed={
                ResearchStatus.SCREENING,
                ResearchStatus.REVISION_REQUIRED,
            },
        )

        candidate.status = (
            ResearchStatus.RESEARCHING
        )
        candidate.verdict = (
            ResearchVerdict.PENDING
        )

        _save_candidate(
            state=state,
            candidate=candidate,
        )

    return candidate


def evaluate_research_candidate(
    *,
    research_id: str,
    verdict: ResearchVerdict,
    evidence: list[str],
    concerns: list[str],
    evaluation_notes: str,
) -> ResearchCandidate:
    cleaned_evidence = _clean_items(
        evidence
    )
    cleaned_concerns = _clean_items(
        concerns
    )
    cleaned_notes = evaluation_notes.strip()

    if not cleaned_notes:
        raise ValueError(
            "evaluation_notes must not be empty."
        )

    if (
        verdict == ResearchVerdict.PROMISING
        and not cleaned_evidence
    ):
        raise ValueError(
            "A promising verdict requires evidence."
        )

    if verdict == ResearchVerdict.PENDING:
        raise ValueError(
            "PENDING is not an evaluation verdict."
        )

    with locked_research_state(
        write=True
    ) as state:
        candidate = _load_candidate(
            state=state,
            research_id=research_id,
        )

        _require_status(
            candidate=candidate,
            allowed={
                ResearchStatus.RESEARCHING,
            },
        )

        candidate.verdict = verdict
        candidate.evidence = cleaned_evidence
        candidate.concerns = cleaned_concerns
        candidate.evaluation_notes = (
            cleaned_notes
        )
        candidate.reviewed_at = utc_now_iso()

        if verdict == ResearchVerdict.PROMISING:
            candidate.status = (
                ResearchStatus.READY_FOR_EXPERIMENT
            )
        elif verdict == ResearchVerdict.INCONCLUSIVE:
            candidate.status = (
                ResearchStatus.REVISION_REQUIRED
            )
        else:
            candidate.status = (
                ResearchStatus.REJECTED
            )

        _save_candidate(
            state=state,
            candidate=candidate,
        )

    return candidate


def archive_research_candidate(
    *,
    research_id: str,
) -> ResearchCandidate:
    with locked_research_state(
        write=True
    ) as state:
        candidate = _load_candidate(
            state=state,
            research_id=research_id,
        )

        _require_status(
            candidate=candidate,
            allowed={
                ResearchStatus.REJECTED,
                ResearchStatus.READY_FOR_EXPERIMENT,
            },
        )

        candidate.status = (
            ResearchStatus.ARCHIVED
        )

        _save_candidate(
            state=state,
            candidate=candidate,
        )

    return candidate
