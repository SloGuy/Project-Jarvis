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
from app.ventures.screening import (
    screen_opportunity,
)
from app.ventures.screening_store import (
    list_screenings,
    save_screening,
)
from app.ventures.research_service import (
    build_initial_research_report,
)
from app.ventures.research_store import (
    get_latest_research_report,
    list_research_reports,
    save_research_report,
)
from app.ventures.research_models import (
    EvidenceQuality,
    EvidenceStatus,
)
from app.ventures.research_updates import update_claim_evidence
from app.ventures.underwriting_models import UnderwritingAssumptions
from app.ventures.underwriting_service import build_underwriting_report
from app.ventures.underwriting_store import (
    list_underwriting_reports,
    save_underwriting_report,
)


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


@router.post(
    "/opportunities/{opportunity_id}/screen"
)
def ventures_screen_opportunity(
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

    result = screen_opportunity(
        opportunity
    )

    record = save_screening(
        opportunity_id=opportunity_id,
        result=result,
    )

    return record


@router.get(
    "/opportunities/{opportunity_id}/screenings"
)
def ventures_opportunity_screenings(
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

    records = list_screenings(
        opportunity_id
    )

    return {
        "opportunity_id": opportunity_id,
        "count": len(records),
        "screenings": records,
    }


@router.post("/opportunities/{opportunity_id}/research")
def ventures_create_research(opportunity_id: str):
    opportunity = get_opportunity(opportunity_id)

    if opportunity is None:
        raise HTTPException(
            status_code=404,
            detail="Ventures opportunity not found.",
        )

    existing = get_latest_research_report(opportunity_id)
    if existing is not None:
        return existing

    report = build_initial_research_report(opportunity)
    return save_research_report(report)


@router.get("/opportunities/{opportunity_id}/research")
def ventures_research_history(opportunity_id: str):
    if get_opportunity(opportunity_id) is None:
        raise HTTPException(
            status_code=404,
            detail="Ventures opportunity not found.",
        )

    records = list_research_reports(opportunity_id)
    return {
        "opportunity_id": opportunity_id,
        "count": len(records),
        "reports": records,
    }


class ClaimEvidenceRequest(BaseModel):
    evidence_status: EvidenceStatus
    evidence_quality: EvidenceQuality
    evidence_notes: str


@router.post(
    "/opportunities/{opportunity_id}/research/claims/{claim_id}/evidence"
)
def ventures_update_claim_evidence(
    opportunity_id: str,
    claim_id: str,
    request: ClaimEvidenceRequest,
):
    if get_opportunity(opportunity_id) is None:
        raise HTTPException(
            status_code=404,
            detail="Ventures opportunity not found.",
        )

    latest = get_latest_research_report(opportunity_id)
    if latest is None:
        raise HTTPException(
            status_code=404,
            detail="Research report not found.",
        )

    if not any(
        claim["claim_id"] == claim_id
        for claim in latest["report"]["claims"]
    ):
        raise HTTPException(
            status_code=404,
            detail="Research claim not found.",
        )

    if not request.evidence_notes.strip():
        raise HTTPException(
            status_code=422,
            detail="Evidence notes must not be blank.",
        )

    return update_claim_evidence(
        opportunity_id=opportunity_id,
        claim_id=claim_id,
        evidence_status=request.evidence_status,
        evidence_quality=request.evidence_quality,
        evidence_notes=request.evidence_notes,
    )


class UnderwritingRequest(BaseModel):
    acquisition_costs_usd: float
    working_capital_usd: float
    annual_added_operating_costs_usd: float
    owner_hourly_cost_usd: float
    automation_hours_saved_per_week: float
    downside_revenue_decline_percent: float
    notes: str

    class Config:
        extra = "forbid"


@router.post("/opportunities/{opportunity_id}/underwriting")
def ventures_create_underwriting(
    opportunity_id: str,
    request: UnderwritingRequest,
):
    opportunity = get_opportunity(opportunity_id)
    if opportunity is None:
        raise HTTPException(
            status_code=404,
            detail="Ventures opportunity not found.",
        )

    research = get_latest_research_report(opportunity_id)

    try:
        assumptions = UnderwritingAssumptions(
            acquisition_costs_usd=request.acquisition_costs_usd,
            working_capital_usd=request.working_capital_usd,
            annual_added_operating_costs_usd=(
                request.annual_added_operating_costs_usd
            ),
            owner_hourly_cost_usd=request.owner_hourly_cost_usd,
            automation_hours_saved_per_week=(
                request.automation_hours_saved_per_week
            ),
            downside_revenue_decline_percent=(
                request.downside_revenue_decline_percent
            ),
            notes=request.notes,
        )
        report = build_underwriting_report(
            opportunity,
            assumptions,
            research_record=research,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    return save_underwriting_report(report)


@router.get("/opportunities/{opportunity_id}/underwriting")
def ventures_underwriting_history(opportunity_id: str):
    if get_opportunity(opportunity_id) is None:
        raise HTTPException(
            status_code=404,
            detail="Ventures opportunity not found.",
        )

    records = list_underwriting_reports(opportunity_id)
    return {
        "opportunity_id": opportunity_id,
        "count": len(records),
        "reports": records,
    }
