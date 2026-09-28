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

    candidate = ResearchCandidate.from_dict(candidate_data)

    if candidate.reviewed_at and not candidate.review_history:
        candidate.review_history.append(
            {
                "review_number": 1,
                "source": "legacy_latest_review",
                "reviewed_at": candidate.reviewed_at,
                "verdict": (
                    candidate.verdict.value
                    if candidate.verdict != ResearchVerdict.PENDING
                    else None
                ),
                "evidence": list(candidate.evidence),
                "concerns": list(candidate.concerns),
                "evaluation_notes": candidate.evaluation_notes,
            }
        )

    return candidate


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
    expected_research_snapshot: dict | None = None,
    request_key: str | None = None,
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

    from copy import deepcopy

    if request_key is not None:
        if (
            not isinstance(request_key, str)
            or not request_key.strip()
            or len(request_key) > 200
        ):
            raise ValueError("Invalid review request key.")
        request_key = request_key.strip()

    expected_research_snapshot = deepcopy(expected_research_snapshot)
    request = {
        "research_id": research_id.strip(),
        "verdict": verdict.value,
        "evidence": cleaned_evidence,
        "concerns": cleaned_concerns,
        "evaluation_notes": cleaned_notes,
        "expected_research_snapshot": expected_research_snapshot,
    }

    with locked_research_state(
        write=True
    ) as state:
        requests = state.get("review_requests", {})
        if not isinstance(requests, dict):
            raise RuntimeError("Invalid research review request store.")
        if request_key is not None and request_key in requests:
            saved = requests[request_key]
            if saved["request"] != request:
                raise ValueError("Review request key has different inputs.")
            if saved["result"]["research_id"] not in state["candidates"]:
                raise RuntimeError("Previously reviewed candidate is missing.")
            return ResearchCandidate.from_dict(deepcopy(saved["result"]))

        candidate = _load_candidate(
            state=state,
            research_id=research_id,
        )

        if expected_research_snapshot is not None:
            from app.capital.validation_plan import research_snapshot
            if research_snapshot(candidate) != expected_research_snapshot:
                raise ValueError("Research changed before review persistence.")

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

        candidate.review_history.append(
            {
                "review_number": len(candidate.review_history) + 1,
                "reviewed_at": candidate.reviewed_at,
                "verdict": candidate.verdict.value,
                "status": candidate.status.value,
                "hypothesis": candidate.hypothesis,
                "success_criteria": list(candidate.success_criteria),
                "evidence": list(candidate.evidence),
                "concerns": list(candidate.concerns),
                "evaluation_notes": candidate.evaluation_notes,
            }
        )

        _save_candidate(
            state=state,
            candidate=candidate,
        )
        if request_key is not None:
            requests[request_key] = {
                "request": deepcopy(request),
                "result": deepcopy(candidate.to_dict()),
            }
            state["review_requests"] = requests

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
