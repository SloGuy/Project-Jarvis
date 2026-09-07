from __future__ import annotations

import fcntl
import hashlib
from contextlib import contextmanager

from app.ventures import assessment_llm as llm
from app.ventures import assessment_store as store
from app.ventures.assessment_models import ASSESSMENT_VERSION
from app.ventures.assessment_interviews import current_interview_inputs
from app.ventures.opportunities import get_opportunity
from app.ventures.research_store import (
    get_latest_research_report,
    research_write_lock,
)


# Bump when input preparation or financial-summary logic changes.
INPUT_VERSION = "compact_financial_summary_interviews_v2"


def assessment_configuration() -> dict:
    return {
        "model": llm.ASSESSMENT_MODEL,
        "assessment_version": ASSESSMENT_VERSION,
        "input_version": INPUT_VERSION,
        "system_prompt_sha256": hashlib.sha256(
            llm.SYSTEM_PROMPT.encode()
        ).hexdigest(),
        "temperature": 0.1,
        "num_predict": 2400,
        "num_ctx": 16384,
        "think": False,
        "format": "json",
        "max_context_characters": llm.MAX_CONTEXT_CHARACTERS,
    }


@contextmanager
def _runner_lock():
    store.ASSESSMENT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = store.ASSESSMENT_DIRECTORY / "runner.lock"

    with path.open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "Another Ventures assessment is already running."
            ) from exc

        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _eligible_opportunity(opportunity_id: str):
    opportunity = get_opportunity(opportunity_id)
    if opportunity is None:
        raise ValueError("Opportunity not found.")
    if opportunity.status.value not in {
        "discovered", "screening", "research"
    }:
        raise ValueError(
            "Opportunity is outside the automatic assessment stages."
        )
    return opportunity


def assess_opportunity(
    opportunity_id: str,
    *,
    expected_assessment_key: str | None = None,
) -> dict:
    with _runner_lock():
        opportunity = _eligible_opportunity(opportunity_id)
        original_opportunity = opportunity.to_dict()
        configuration = assessment_configuration()

        with research_write_lock():
            research = get_latest_research_report(opportunity_id)
            if research is None:
                raise ValueError("Research report is required.")

            interview_inputs = current_interview_inputs(opportunity_id)
            key = store.assessment_key(
                research_record=research,
                configuration=configuration,
                interview_inputs=interview_inputs,
            )

            if (
                expected_assessment_key is not None
                and key != expected_assessment_key
            ):
                raise ValueError(
                    "Research or configuration changed after selection."
                )

            existing = store.get_assessment(key)
            if existing is not None:
                return {
                    "status": "cached",
                    "record": existing,
                }

        # Do not hold the research lock during a slow model request.
        if interview_inputs:
            result = llm.assess_research_report(
                research,
                interview_inputs=interview_inputs,
            )
        else:
            result = llm.assess_research_report(research)

        with research_write_lock():
            current = get_latest_research_report(opportunity_id)
            if current != research:
                raise ValueError(
                    "Research changed during generation; "
                    "assessment was not saved. Retry with current research."
                )

            current_opportunity = _eligible_opportunity(opportunity_id)
            if current_opportunity.to_dict() != original_opportunity:
                raise ValueError(
                    "Opportunity changed during generation; "
                    "assessment was not saved."
                )

            if assessment_configuration() != configuration:
                raise ValueError(
                    "Assessment configuration changed during generation."
                )

            if current_interview_inputs(opportunity_id) != interview_inputs:
                raise ValueError(
                    "Interview inputs changed during generation; "
                    "assessment was not saved."
                )

            record = store.save_assessment(
                research_record=research,
                configuration=configuration,
                result=result,
                interview_inputs=interview_inputs,
            )

        return {
            "status": "created",
            "record": record,
        }
