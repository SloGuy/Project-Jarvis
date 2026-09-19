"""Read-only, digest-bound factory review packets. No approval authority."""
import hashlib
import json

from app.market_db.database import SessionLocal
from app.capital.experiment_factory_store import ExperimentFactoryRecord
from app.capital.experiment_factory_review import build_factory_review


def packet_digest(payload):
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def prepare_factory_packet(request_key):
    with SessionLocal() as session:
        record = session.get(ExperimentFactoryRecord, request_key)
        if record is None:
            raise KeyError("Factory request not found.")
        if record.status != "awaiting_review":
            raise ValueError("Factory request is not awaiting review.")
        request = {
            "request_key": record.request_key,
            "research_id": record.research_id,
            "requested_by": record.requested_by,
        }

    review = build_factory_review(request["research_id"])
    if review["eligible_for_operator_review"] is not True:
        reasons = "; ".join(review["blockers"])
        raise ValueError(f"Factory request is blocked: {reasons}")
    if review["research"]["research_id"] != request["research_id"]:
        raise ValueError("Review belongs to a different candidate.")
    if any(
        review.get(field) is not False
        for field in (
            "creation_authorized",
            "execution_authorized",
            "live_capital_authorized",
        )
    ):
        raise ValueError("Unexpected authority in review packet.")
    if review.get("human_approval_required") is not True:
        raise ValueError("Human approval must remain required.")

    payload = {
        "schema_version": 1,
        "action": "create_inactive_paper_experiment",
        "request": request,
        "review": review,
    }
    return {
        "payload": payload,
        "sha256": packet_digest(payload),
    }
