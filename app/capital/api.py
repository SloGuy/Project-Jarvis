from fastapi import APIRouter, Query

from app.capital.status_service import (
    get_capital_status,
)
from app.capital.performance_lab import (
    get_performance_lab,
)
from app.capital.strategy_ranking import (
    get_strategy_rankings,
)
from app.capital.research_api import (
    router as research_router,
)
from app.capital.committee_service import (
    get_capital_committee,
)
from app.capital.safety_audit import (
    get_capital_safety_audit,
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


@router.get("/rankings")
def capital_rankings() -> dict:
    return get_strategy_rankings()


@router.get("/committee")
def capital_committee() -> dict:
    return get_capital_committee()


@router.get("/audit")
def capital_audit() -> dict:
    return get_capital_safety_audit()
