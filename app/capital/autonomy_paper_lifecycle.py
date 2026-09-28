"""Policy-controlled transitions for autonomous paper experiments.

Allocation changes are entry limits, never cash deposits.
The execution worker must enforce lifecycle state before placing orders.
"""

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select, text

from app.market_db.database import SessionLocal
from app.market_db.models import Portfolio, PortfolioPosition
from app.capital.autonomy_control import read_operating_policy
from app.capital.autonomy_policy import authorize_capital_action
from app.capital.experiment_factory_packet import packet_digest
from app.capital.experiment_factory_review import build_factory_review
from app.capital.experiment_factory_store import ExperimentFactoryRecord
from app.capital.paper_lifecycle_store import PaperLifecycleRecord


AGENT = "capital.lifecycle"
TOTAL_ALLOCATION = Decimal("1000")
ACTIONS = {
    "active": "capital.promote_paper",
    "paused": "capital.pause_paper",
    "demoted": "capital.demote_paper",
    "retired": "capital.retire_paper",
}
ALLOWED = {
    "planned": {"active", "paused", "demoted", "retired"},
    "active": {"paused", "demoted", "retired"},
    "paused": {"active", "demoted", "retired"},
    "demoted": {"active", "paused", "retired"},
    "retired": set(),
}


def authorize(target):
    return authorize_capital_action(
        agent_id=AGENT,
        action=ACTIONS[target],
        policy=read_operating_policy(),
        execution_mode="paper",
    )


def transition_paper_experiment(
    *,
    request_key,
    target,
    decision_key,
    expected_version,
    reason,
):
    for name, value, maximum in (
        ("request_key", request_key, 100),
        ("decision_key", decision_key, 200),
        ("reason", reason, 2000),
    ):
        if (
            not isinstance(value, str)
            or not value.strip()
            or value != value.strip()
            or len(value) > maximum
        ):
            raise ValueError(f"Invalid {name}.")
    if target not in ACTIONS:
        raise ValueError("Unsupported lifecycle target.")
    if type(expected_version) is not int or expected_version < 1:
        raise ValueError("Expected version must be a positive integer.")
    authorize(target)
    binding = {
        "target": target,
        "expected_version": expected_version,
        "reason": reason,
    }

    with SessionLocal() as session:
        with session.begin():
            if session.get_bind().dialect.name != "postgresql":
                raise ValueError("Lifecycle transitions require PostgreSQL.")
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            session.execute(text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended('jarvis:paper-lifecycle:capacity', 0))"
            ))
            lifecycle = session.scalar(
                select(PaperLifecycleRecord)
                .where(PaperLifecycleRecord.request_key == request_key)
                .with_for_update()
            )
            if lifecycle is None:
                raise KeyError("Autonomous lifecycle record not found.")

            history = deepcopy(lifecycle.transition_history)
            previous = [
                item for item in history
                if item.get("decision_key") == decision_key
            ]
            if previous:
                if len(previous) != 1 or previous[0]["binding"] != binding:
                    raise ValueError("Decision key has conflicting inputs.")
                # Return the saved receipt without replaying the transition.
                return deepcopy(previous[0]["result"])

            if lifecycle.version != expected_version:
                raise ValueError("Lifecycle changed; reassess before deciding.")
            if target not in ALLOWED[lifecycle.status]:
                raise ValueError("Lifecycle transition is not allowed.")

            record = session.get(ExperimentFactoryRecord, request_key)
            portfolio = session.scalar(
                select(Portfolio)
                .where(Portfolio.id == lifecycle.portfolio_id)
                .with_for_update()
            )
            if (
                record is None
                or record.status != "created"
                or record.approved_by != AGENT
                or record.portfolio_id != lifecycle.portfolio_id
                or record.approval_snapshot != lifecycle.authorization_snapshot
                or portfolio is None
                or portfolio.portfolio_type != "paper"
                or lifecycle.execution_mode != "paper"
            ):
                raise ValueError("Factory and paper portfolio binding differs.")

            snapshot = record.approval_snapshot
            if packet_digest(snapshot["packet"]) != record.approval_sha256:
                raise ValueError("Saved creation packet integrity differs.")
            if portfolio.name != snapshot["experiment"]["portfolio_name"]:
                raise ValueError("Portfolio identity differs.")

            allocation = Decimal("0")
            if target == "active":
                review = build_factory_review(record.research_id)
                if (
                    review["eligible_for_operator_review"] is not True
                    or review["blockers"]
                    or review["validation_gate"]["status"] != "passed"
                    or not review["verified_plan_bindings"]
                    or packet_digest(review)
                    != packet_digest(snapshot["packet"]["review"])
                ):
                    raise ValueError("Activation evidence changed or is blocked.")

                allocation = Decimal(
                    snapshot["experiment"]["starting_capital_usd"]
                )
                if not allocation.is_finite() or allocation != TOTAL_ALLOCATION:
                    raise ValueError("Unsupported paper allocation.")
                allocated = session.scalar(
                    select(func.coalesce(
                        func.sum(PaperLifecycleRecord.allocation_usd), 0
                    )).where(
                        PaperLifecycleRecord.request_key != request_key
                    )
                )
                if allocated + allocation > TOTAL_ALLOCATION:
                    raise ValueError("Autonomous allocation capacity is full.")

            if target == "retired":
                holdings = session.scalar(
                    select(func.count()).select_from(PortfolioPosition).where(
                        PortfolioPosition.portfolio_id == portfolio.id,
                        PortfolioPosition.quantity != 0,
                    )
                )
                if holdings:
                    raise ValueError("Close remaining holdings before retirement.")

            authorization = authorize(target)
            old_status = lifecycle.status
            lifecycle.status = target
            lifecycle.allocation_usd = allocation
            lifecycle.policy_version = authorization["policy_version"]
            # Paused/demoted accounts remain accessible to protective exits.
            if target == "active":
                portfolio.is_active = True
            elif target == "retired":
                portfolio.is_active = False

            response = {
                "request_key": request_key,
                "decision_key": decision_key,
                "status": target,
                "version": expected_version + 1,
                "allocation_usd": str(allocation),
                "entry_enabled": target == "active",
                "live_capital_authorized": False,
            }
            history.append({
                "at": datetime.now(timezone.utc).isoformat(),
                "actor": AGENT,
                "from": old_status,
                "to": target,
                "decision_key": decision_key,
                "binding": binding,
                "authorization": authorization,
                "result": deepcopy(response),
            })
            lifecycle.transition_history = history
            session.flush()
            if lifecycle.version != response["version"]:
                raise RuntimeError("Unexpected lifecycle version.")
        return response
