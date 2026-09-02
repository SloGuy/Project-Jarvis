from fastapi import APIRouter, Query

from app.capital.status_service import (
    get_capital_status,
)
from app.capital.performance_lab import (
    get_performance_lab,
)
from app.capital.research_api import (
    router as research_router,
)


router = APIRouter(
    prefix="/capital",
    tags=["capital"],
)


router.include_router(
    research_router
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


@router.get("/performance")
def capital_performance() -> dict:
    return get_performance_lab()
