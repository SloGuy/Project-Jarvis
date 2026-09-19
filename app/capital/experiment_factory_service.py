"""Paper experiment factory submission; no approval or execution authority."""
from datetime import timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.market_db.database import SessionLocal
from app.capital.experiment_factory_models import ExperimentFactoryRequest
from app.capital.experiment_factory_store import ExperimentFactoryRecord
from app.capital.research_service import require_research_candidate


def request_summary(record):
    created_at = record.created_at
    if created_at.tzinfo is None:
        # Factory timestamps are generated in UTC; SQLite reloads them naive.
        created_at = created_at.replace(tzinfo=timezone.utc)
    created_at = created_at.astimezone(timezone.utc)

    return {
        "request_key": record.request_key,
        "research_id": record.research_id,
        "requested_by": record.requested_by,
        "status": record.status,
        "created_at": created_at.isoformat(),
        "portfolio_id": record.portfolio_id,
        "live_capital_authorized": False,
        "execution_authorized": False,
    }


def find_existing(session, request):
    record = session.get(ExperimentFactoryRecord, request.request_key)
    if record is not None:
        if (
            record.research_id != request.research_id
            or record.requested_by != request.requested_by
        ):
            raise ValueError("Request key is already bound to different inputs.")
        return record

    candidate_request = session.scalar(
        select(ExperimentFactoryRecord).where(
            ExperimentFactoryRecord.research_id == request.research_id
        )
    )
    if candidate_request is not None:
        raise ValueError("This candidate already has a factory request.")
    return None


def submit_factory_request(request: ExperimentFactoryRequest):
    request = ExperimentFactoryRequest.model_validate(request.model_dump())

    # An identical retry returns the durable result without updating it.
    with SessionLocal() as session:
        existing = find_existing(session, request)
        if existing is not None:
            return request_summary(existing)

    candidate = require_research_candidate(research_id=request.research_id)
    if candidate.status.value not in {
        "proposed", "screening", "researching", "ready_for_experiment",
    }:
        raise ValueError("Candidate is not eligible for factory submission.")

    # Eligibility and evidence must be checked again during human review.
    try:
        with SessionLocal() as session:
            with session.begin():
                existing = find_existing(session, request)
                if existing is not None:
                    return request_summary(existing)
                record = ExperimentFactoryRecord(
                    request_key=request.request_key,
                    research_id=request.research_id,
                    requested_by=request.requested_by,
                    status="awaiting_review",
                )
                session.add(record)
                session.flush()
                result = request_summary(record)
            return result
    except IntegrityError:
        # Another submission may have committed after our lookup.
        # Inspect only after the failed transaction has rolled back.
        with SessionLocal() as session:
            existing = find_existing(session, request)
            if existing is not None:
                return request_summary(existing)
        raise


def get_factory_request(request_key):
    if not isinstance(request_key, str) or not request_key.strip():
        raise ValueError("Request key must be nonblank text.")

    with SessionLocal() as session:
        record = session.get(ExperimentFactoryRecord, request_key.strip())
        if record is None:
            raise KeyError("Factory request not found.")
        result = request_summary(record)
        result["human_approval_required"] = True
        result["experiment"] = None
        if record.status == "created":
            from copy import deepcopy

            result["experiment"] = deepcopy(
                record.approval_snapshot["experiment"]
            )
        return result
