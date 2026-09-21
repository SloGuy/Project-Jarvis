"""Read-only API for indicative portfolio intelligence."""

import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.capital.portfolio_intelligence_service import (
    get_portfolio_intelligence,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/portfolio-intelligence",
    tags=["capital"],
)


@router.get("")
def portfolio_intelligence() -> dict:
    try:
        return get_portfolio_intelligence()
    except (ValueError, KeyError, RuntimeError, SQLAlchemyError) as error:
        logger.exception("Portfolio intelligence could not be generated.")
        raise HTTPException(
            status_code=503,
            detail=(
                "Portfolio intelligence is currently unavailable. "
                "Required data could not be read or validated."
            ),
        ) from error
