from fastapi import APIRouter, Query

from app.capital.status_service import (
    get_capital_status,
)


router = APIRouter(
    prefix="/capital",
    tags=["capital"],
)


@router.get("/status")
def capital_status(
    portfolio_id: int | None = None,
    decision_limit: int = Query(
        default=10,
        ge=1,
        le=100,
    ),
) -> dict:
    return get_capital_status(
        portfolio_id=portfolio_id,
        decision_limit=decision_limit,
    )
