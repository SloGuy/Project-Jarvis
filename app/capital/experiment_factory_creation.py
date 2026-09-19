"""Creation backend for the trusted local operator command.

Not an agent API. Caller-supplied confirmation data is not a credential.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import os
import pwd

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import Portfolio
from app.capital.experiment_factory_store import ExperimentFactoryRecord
from app.capital.experiment_factory_packet import (
    packet_digest,
    prepare_factory_packet,
)


def creation_result(record):
    return {
        "request_key": record.request_key,
        "status": record.status,
        "portfolio_id": record.portfolio_id,
        "experiment": deepcopy(record.approval_snapshot["experiment"]),
        "execution_authorized": False,
        "live_capital_authorized": False,
    }


def create_confirmed_experiment(confirmation):
    """Called only after interactive confirmation in the operator workflow."""
    if confirmation.get("action") != "create_inactive_paper_experiment":
        raise ValueError("Unsupported confirmation action.")
    if (
        confirmation.get("operator_uid") != os.getuid()
        or confirmation.get("operator") != pwd.getpwuid(os.getuid()).pw_name
    ):
        raise PermissionError("Confirmation belongs to another operator account.")

    confirmed_at = datetime.fromisoformat(confirmation["confirmed_at"])
    now = datetime.now(timezone.utc)
    if (
        confirmed_at.utcoffset() is None
        or not timedelta(0) <= now - confirmed_at <= timedelta(minutes=10)
    ):
        raise ValueError("Confirmation expired or has an invalid timestamp.")

    request_key = confirmation["request_key"]
    expected = confirmation["packet_sha256"]

    with SessionLocal() as session:
        with session.begin():
            record = session.scalar(
                select(ExperimentFactoryRecord)
                .where(ExperimentFactoryRecord.request_key == request_key)
                .with_for_update()
            )
            if record is None:
                raise KeyError("Factory request not found.")

            if record.status == "created":
                if (
                    record.approval_sha256 != expected
                    or packet_digest(record.approval_snapshot["packet"]) != expected
                ):
                    raise ValueError("Existing creation has different approval.")
                return creation_result(record)

            if record.status != "awaiting_review":
                raise ValueError("Factory request is not awaiting review.")

            # Reverify evidence/configuration after operator confirmation.
            packet = prepare_factory_packet(request_key)
            if (
                packet["sha256"] != expected
                or packet_digest(packet["payload"]) != expected
            ):
                raise ValueError("Review changed; a new confirmation is required.")

            payload = packet["payload"]
            request = payload["request"]
            if request != {
                "request_key": record.request_key,
                "research_id": record.research_id,
                "requested_by": record.requested_by,
            }:
                raise ValueError("Locked request differs from reviewed request.")

            review = payload["review"]
            proposed = review["proposed_experiment"]
            if (
                proposed["status"] != "planned"
                or proposed["execution_mode"] != "disabled"
                or proposed["portfolio_type"] != "paper"
                or proposed["portfolio_active"] is not False
            ):
                raise ValueError("Only inactive paper experiments may be created.")

            capital = Decimal(proposed["starting_capital_usd"])
            if not capital.is_finite() or capital <= 0:
                raise ValueError("Starting capital must be finite and positive.")

            identity = hashlib.sha256(request_key.encode("utf-8")).hexdigest()[:32]
            portfolio_name = f"Jarvis Factory - {identity}"
            collision = session.scalar(
                select(Portfolio.id).where(Portfolio.name == portfolio_name)
            )
            if collision is not None:
                raise ValueError("Reserved portfolio name already exists.")

            portfolio = Portfolio(
                name=portfolio_name,
                portfolio_type="paper",
                cash_balance_usd=capital,
                is_active=False,
            )
            session.add(portfolio)
            session.flush()

            experiment = {
                "experiment_id": f"factory_{identity}",
                "name": f"Paper experiment for {record.research_id}",
                "strategy_name": review["research"]["strategy_name"],
                "strategy_version": review["strategy"]["version"],
                "research_id": record.research_id,
                "hypothesis_version": review["research"]["hypothesis_version"],
                "portfolio_id": portfolio.id,
                "portfolio_name": portfolio_name,
                "status": "planned",
                "execution_mode": "disabled",
                "started_at": None,
                "duration_days": proposed["duration_days"],
                "starting_capital_usd": str(capital),
                "risk_policy_name": proposed["policy"]["name"],
            }
            record.portfolio_id = portfolio.id
            record.approved_by = confirmation["operator"]
            record.approved_at = confirmed_at
            record.approval_sha256 = expected
            record.approval_snapshot = {
                "packet": deepcopy(payload),
                "confirmation": deepcopy(confirmation),
                "experiment": experiment,
            }
            record.status = "created"
            session.flush()
            result = creation_result(record)
        return result
