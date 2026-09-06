from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.ventures.models import (
    BusinessType,
    OpportunityStatus,
)
from app.ventures.opportunities import (
    create_opportunity,
    get_opportunity,
    list_opportunities,
    update_opportunity_status,
)
from app.ventures.thesis import get_active_thesis


router = APIRouter(
    prefix="/ventures",
    tags=["ventures"],
)


class OpportunityCreateRequest(BaseModel):
    name: str
    business_type: BusinessType
    asking_price_usd: float
    annual_revenue_usd: float | None = None
    annual_sde_usd: float | None = None
    owner_hours_per_week: float | None = None
    source: str | None = None
    source_url: str | None = None
    notes: str | None = None


class OpportunityStatusRequest(BaseModel):
    status: OpportunityStatus


@router.get("/thesis")
def ventures_thesis():
    return get_active_thesis().to_dict()


@router.get("/opportunities")
def ventures_opportunities():
    opportunities = list_opportunities()

    return {
        "count": len(opportunities),
        "opportunities": [
            opportunity.to_dict()
            for opportunity in opportunities
        ],
    }


@router.get("/opportunities/{opportunity_id}")
def ventures_opportunity(
    opportunity_id: str,
):
    opportunity = get_opportunity(
        opportunity_id
    )

    if opportunity is None:
        raise HTTPException(
            status_code=404,
            detail="Ventures opportunity not found.",
        )

    return opportunity.to_dict()


@router.post("/opportunities")
def ventures_create_opportunity(
    request: OpportunityCreateRequest,
):
    opportunity = create_opportunity(
        name=request.name,
        business_type=request.business_type,
        asking_price_usd=request.asking_price_usd,
        annual_revenue_usd=request.annual_revenue_usd,
        annual_sde_usd=request.annual_sde_usd,
        owner_hours_per_week=request.owner_hours_per_week,
        source=request.source,
        source_url=request.source_url,
        notes=request.notes,
    )

    return opportunity.to_dict()


@router.post(
    "/opportunities/{opportunity_id}/status"
)
def ventures_update_status(
    opportunity_id: str,
    request: OpportunityStatusRequest,
):
    try:
        opportunity = update_opportunity_status(
            opportunity_id,
            request.status,
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Ventures opportunity not found.",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )

    return opportunity.to_dict()
