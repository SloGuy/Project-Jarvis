"""Policy-authorized creation of inactive paper experiments.

Local worker backend, not an HTTP endpoint. Agent-supplied approval
objects are never accepted. Activation is a separate lifecycle action.
"""

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib

from sqlalchemy import func, select, text

from app.market_db.database import SessionLocal
from app.market_db.models import Portfolio
from app.capital.autonomy_control import read_operating_policy
from app.capital.autonomy_policy import authorize_capital_action
from app.capital.experiment_factory_packet import packet_digest
from app.capital.experiment_factory_review import build_factory_review
from app.capital.experiment_factory_store import ExperimentFactoryRecord
from app.capital.paper_lifecycle_store import PaperLifecycleRecord


AGENT = "capital.lifecycle"
CREATION_RULE = "single_mr2_btc_paper_1000_v1"
STARTING_CASH = Decimal("1000")
MAX_OPEN_EXPERIMENTS = 1


def authorize():
    return authorize_capital_action(
        agent_id=AGENT,
        action="capital.create_paper_experiment",
        policy=read_operating_policy(),
        execution_mode="paper",
    )


def result(record, lifecycle):
    return {
        "request_key": record.request_key,
        "status": "created",
        "portfolio_id": record.portfolio_id,
        "lifecycle_status": lifecycle.status,
        "authorization_basis": "operating_policy",
        "execution_authorized": False,
        "live_capital_authorized": False,
    }


def create_autonomous_paper_experiment(request_key):
    if (
        not isinstance(request_key, str)
        or not request_key.strip()
        or request_key != request_key.strip()
        or len(request_key) > 100
    ):
        raise ValueError("Invalid factory request key.")
    authorize()

    with SessionLocal() as session:
        with session.begin():
            if session.get_bind().dialect.name != "postgresql":
                raise ValueError("Autonomous creation requires PostgreSQL.")

            # All autonomous creators share this transaction-scoped lock.
            # Lifecycle capacity changes must use the same lock.
            session.execute(text(
                "SET LOCAL lock_timeout = '5s'"
            ))
            session.execute(text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended('jarvis:paper-lifecycle:capacity', 0))"
            ))
            record = session.scalar(
                select(ExperimentFactoryRecord)
                .where(ExperimentFactoryRecord.request_key == request_key)
                .with_for_update()
            )
            if record is None:
                raise KeyError("Factory request not found.")
            lifecycle = session.get(PaperLifecycleRecord, request_key)

            if record.status == "created":
                if (
                    lifecycle is None
                    or lifecycle.portfolio_id != record.portfolio_id
                    or record.approved_by != AGENT
                    or lifecycle.authorization_snapshot
                    != record.approval_snapshot
                    or packet_digest(record.approval_snapshot["packet"])
                    != record.approval_sha256
                ):
                    raise ValueError("Existing creation binding differs.")
                return result(record, lifecycle)

            if record.status != "awaiting_review" or lifecycle is not None:
                raise ValueError("Request is not available for creation.")

            count = session.scalar(
                select(func.count()).select_from(PaperLifecycleRecord)
                .where(PaperLifecycleRecord.status != "retired")
            )
            if count >= MAX_OPEN_EXPERIMENTS:
                raise ValueError("Autonomous experiment capacity is full.")

            review = build_factory_review(record.research_id)
            if (
                review["eligible_for_operator_review"] is not True
                or review["blockers"]
                or review["validation_gate"]["status"] != "passed"
                or not review["verified_plan_bindings"]
            ):
                raise ValueError("Verified research and validation are required.")
            research = review["research"]
            proposed = review["proposed_experiment"]
            if (
                research["research_id"] != record.research_id
                or research["strategy_name"] != "mean_reversion_v2"
                or research["asset_universe"] != ["BTC"]
                or proposed["portfolio_type"] != "paper"
                or proposed["portfolio_active"] is not False
                or proposed["status"] != "planned"
                or proposed["execution_mode"] != "disabled"
                or any(review.get(key) is not False for key in (
                    "creation_authorized",
                    "execution_authorized",
                    "live_capital_authorized",
                ))
            ):
                raise ValueError("Review is outside the supported paper scope.")

            cash = Decimal(proposed["starting_capital_usd"])
            if not cash.is_finite() or cash != STARTING_CASH:
                raise ValueError("This creation rule requires $1000 paper cash.")

            authorization = authorize()
            now = datetime.now(timezone.utc)
            identity = hashlib.sha256(request_key.encode()).hexdigest()[:32]
            name = f"Jarvis Factory - {identity}"
            if session.scalar(select(Portfolio.id).where(
                Portfolio.name == name
            )) is not None:
                raise ValueError("Reserved portfolio name already exists.")

            portfolio = Portfolio(
                name=name, portfolio_type="paper",
                cash_balance_usd=cash, is_active=False,
            )
            session.add(portfolio)
            session.flush()

            experiment = {
                "experiment_id": f"factory_{identity}",
                "name": f"Paper experiment for {record.research_id}",
                "strategy_name": research["strategy_name"],
                "strategy_version": review["strategy"]["version"],
                "research_id": record.research_id,
                "hypothesis_version": research["hypothesis_version"],
                "portfolio_id": portfolio.id,
                "portfolio_name": name,
                "status": "planned",
                "execution_mode": "disabled",
                "started_at": None,
                "duration_days": proposed["duration_days"],
                "starting_capital_usd": str(cash),
                "risk_policy_name": proposed["policy"]["name"],
            }
            packet = {
                "schema_version": 1,
                "action": "create_inactive_paper_experiment",
                "request": {
                    "request_key": request_key,
                    "research_id": record.research_id,
                    "requested_by": record.requested_by,
                },
                "review": deepcopy(review),
            }
            snapshot = {
                "packet": packet,
                "authorization": {
                    **authorization,
                    "basis": "operating_policy",
                    "creation_rule": CREATION_RULE,
                    "authorized_at": now.isoformat(),
                    "human_approval_recorded": False,
                    "creation_authorized": True,
                    "execution_authorized": False,
                },
                "experiment": experiment,
            }
            record.portfolio_id = portfolio.id
            record.approved_by = AGENT
            record.approved_at = now
            record.approval_sha256 = packet_digest(packet)
            record.approval_snapshot = deepcopy(snapshot)
            record.status = "created"
            lifecycle = PaperLifecycleRecord(
                request_key=request_key,
                portfolio_id=portfolio.id,
                status="planned",
                allocation_usd=Decimal("0"),
                policy_version=authorization["policy_version"],
                authorization_snapshot=deepcopy(snapshot),
                transition_history=[{
                    "at": now.isoformat(),
                    "from": None,
                    "to": "planned",
                    "actor": AGENT,
                    "reason": CREATION_RULE,
                }],
            )
            session.add(lifecycle)
            session.flush()
            response = result(record, lifecycle)
        return response
