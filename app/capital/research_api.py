from fastapi import (
    APIRouter,
    HTTPException,
    Query,
)
from pydantic import BaseModel, Field

from app.capital.research_models import (
    ResearchStatus,
    ResearchVerdict,
)
from app.capital.research_service import (
    get_research_summary,
    propose_research_candidate,
    require_research_candidate,
)
from app.capital.research_workflow import (
    begin_research_screening,
    begin_strategy_research,
    evaluate_research_candidate,
)
from app.capital.research_revision import revise_research_candidate


router = APIRouter(
    prefix="/research",
    tags=["capital-research"],
)


class ResearchProposalRequest(BaseModel):
    strategy_name: str = Field(
        min_length=1
    )
    display_name: str = Field(
        min_length=1
    )
    hypothesis: str = Field(
        min_length=1
    )
    description: str = Field(
        min_length=1
    )
    market_regime: str = Field(
        min_length=1
    )
    asset_universe: list[str] = Field(
        min_length=1
    )
    data_requirements: list[str] = Field(
        min_length=1
    )
    risk_thesis: str = Field(
        min_length=1
    )
    success_criteria: list[str] = Field(
        min_length=1
    )
    proposed_by: str = Field(
        min_length=1
    )


class ResearchEvaluationRequest(BaseModel):
    verdict: ResearchVerdict
    evidence: list[str] = Field(
        default_factory=list
    )
    concerns: list[str] = Field(
        default_factory=list
    )
    evaluation_notes: str = Field(
        min_length=1
    )


def _bad_request(
    error: Exception,
) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail=str(error),
    )


@router.get("")
def research_candidates(
    status: ResearchStatus | None = Query(
        default=None
    ),
) -> dict:
    summary = get_research_summary()

    if status is not None:
        summary["candidates"] = [
            candidate
            for candidate
            in summary["candidates"]
            if candidate["status"] == status.value
        ]
        summary["filtered_status"] = (
            status.value
        )
        summary["filtered_count"] = len(
            summary["candidates"]
        )

    return summary


@router.get("/{research_id}")
def research_candidate(
    research_id: str,
) -> dict:
    try:
        candidate = require_research_candidate(
            research_id=research_id,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    return candidate.to_dict()


@router.post("")
def create_research_candidate(
    request: ResearchProposalRequest,
) -> dict:
    try:
        candidate = propose_research_candidate(
            **request.model_dump()
        )
    except ValueError as error:
        raise _bad_request(error) from error

    return candidate.to_dict()


@router.post("/{research_id}/screen")
def screen_research_candidate(
    research_id: str,
) -> dict:
    try:
        candidate = begin_research_screening(
            research_id=research_id,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise _bad_request(error) from error

    return candidate.to_dict()


@router.post("/{research_id}/begin")
def begin_research_candidate(
    research_id: str,
) -> dict:
    try:
        candidate = begin_strategy_research(
            research_id=research_id,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise _bad_request(error) from error

    return candidate.to_dict()


@router.post("/{research_id}/evaluate")
def evaluate_candidate(
    research_id: str,
    request: ResearchEvaluationRequest,
) -> dict:
    try:
        candidate = evaluate_research_candidate(
            research_id=research_id,
            **request.model_dump(),
        )
    except KeyError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise _bad_request(error) from error

    return candidate.to_dict()


class ResearchRevisionRequest(BaseModel):
    strategy_name: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    revision_reason: str = Field(min_length=1)


@router.post("/{research_id}/revise")
def revise_candidate(
    research_id: str,
    request: ResearchRevisionRequest,
) -> dict:
    try:
        candidate = revise_research_candidate(
            parent_research_id=research_id,
            **request.model_dump(),
        )
    except KeyError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise _bad_request(error) from error

    return candidate.to_dict()


@router.get("/{research_id}/validations/{plan_id}/recommendation")
def research_validation_recommendation(
    research_id: str,
    plan_id: str,
) -> dict:
    from app.capital.validation_registry import get_plan
    from app.capital.validation_research import inspect_recommendation

    try:
        require_research_candidate(research_id=research_id)
        registered = get_plan(plan_id)
        bound_id = registered["envelope"]["plan"]["research"]["research_id"]
        if bound_id != research_id:
            raise HTTPException(
                status_code=404,
                detail="Validation plan does not belong to this candidate.",
            )
        return inspect_recommendation(plan_id)
    except KeyError as error:
        raise HTTPException(
            status_code=404,
            detail="Research candidate or validation record not found.",
        ) from error
    except (ValueError, RuntimeError, OSError) as error:
        raise HTTPException(
            status_code=409,
            detail="Validation evidence cannot currently be verified.",
        ) from error
