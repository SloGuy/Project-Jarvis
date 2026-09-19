"""Factory submission and read-only review API.

Requester attribution is not authenticated identity.
Approval and creation are deliberately absent from this router.
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.capital.experiment_factory_models import ExperimentFactoryRequest
from app.capital.experiment_factory_service import (
    get_factory_request,
    submit_factory_request,
)
from app.capital.experiment_factory_review import build_factory_review


router = APIRouter(
    prefix="/experiment-factory",
    tags=["capital-experiment-factory"],
)


def factory_call(function, *args):
    try:
        return function(*args)
    except KeyError as error:
        raise HTTPException(
            status_code=404,
            detail="Factory request or research record not found.",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error
    except (SQLAlchemyError, RuntimeError, OSError) as error:
        raise HTTPException(
            status_code=503,
            detail="Factory state or evidence is currently unavailable.",
        ) from error


@router.post("")
def submit_request(request: ExperimentFactoryRequest) -> dict:
    return factory_call(submit_factory_request, request)


@router.get("/{request_key}")
def request_status(request_key: str) -> dict:
    return factory_call(get_factory_request, request_key)


@router.get("/{request_key}/review")
def request_review(request_key: str) -> dict:
    request = factory_call(get_factory_request, request_key)
    review = factory_call(build_factory_review, request["research_id"])
    return {
        "request": request,
        "current_eligibility": review,
        "human_approval_required": True,
        "creation_authorized": False,
        "execution_authorized": False,
        "live_capital_authorized": False,
    }
