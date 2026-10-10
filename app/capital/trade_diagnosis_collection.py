"""Read-only collection of experiment-bound trade diagnostics."""

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json

from app.capital.trade_diagnosis import build_trade_diagnosis


QUERY_LIMIT = 10000


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False, default=str,
    )


def _authorize():
    from app.agents.capital_registry import RESEARCH_AGENT_ID
    from app.capital.autonomy_control import read_operating_policy
    from app.capital.autonomy_policy import authorize_capital_action

    authorize_capital_action(
        agent_id=RESEARCH_AGENT_ID,
        action="capital.inspect_evidence",
        policy=read_operating_policy(),
        execution_mode="paper",
    )


def read_experiment(experiment_id):
    from app.capital.experiment_registry import require_experiment

    return require_experiment(experiment_id=experiment_id).to_dict()


def read_portfolio(portfolio_name):
    from sqlalchemy import select
    from app.market_db.database import SessionLocal
    from app.market_db.models import Portfolio

    with SessionLocal() as session:
        rows = session.scalars(
            select(Portfolio).where(
                Portfolio.name == portfolio_name,
                Portfolio.is_active.is_(True),
            )
        ).all()
        if len(rows) != 1:
            raise ValueError("Expected exactly one active experiment portfolio.")
        portfolio = rows[0]
        if portfolio.portfolio_type != "paper":
            raise ValueError("Diagnostic collection requires a paper portfolio.")
        return {
            "id": portfolio.id,
            "name": portfolio.name,
            "portfolio_type": portfolio.portfolio_type,
        }


def read_risk_policy(policy_name):
    from app.autonomous_trading.policy import INITIAL_1000_POLICY, RiskPolicy
    from app.capital import policies

    candidates = [
        INITIAL_1000_POLICY,
        *[
            value for value in vars(policies).values()
            if isinstance(value, RiskPolicy)
        ],
    ]
    matching = [
        asdict(policy) for policy in candidates
        if policy.name == policy_name
    ]
    if not matching:
        raise ValueError("Experiment risk policy cannot be resolved.")
    if len({_canonical(value) for value in matching}) != 1:
        raise ValueError("Conflicting definitions of the experiment risk policy.")
    return matching[0]


def read_committee_thresholds():
    from app.capital.graduation_gates import (
        MINIMUM_CLOSED_TRADES,
        MINIMUM_PROFIT_FACTOR,
    )

    return {
        "minimum_closed_trades": MINIMUM_CLOSED_TRADES,
        "minimum_profit_factor": str(MINIMUM_PROFIT_FACTOR),
    }


def read_closed_journals(portfolio_id):
    from app.autonomous_trading.journal_queries import get_trade_journal

    # The committee includes the portfolio's journals. Do not silently
    # remove rows attributed to another strategy; the builder rejects them.
    return get_trade_journal(
        status="closed",
        limit=QUERY_LIMIT,
        portfolio_id=portfolio_id,
    )


def _binding(experiment_id):
    experiment = read_experiment(experiment_id)
    if experiment.get("experiment_id") != experiment_id:
        raise ValueError("Experiment identity mismatch.")
    if experiment.get("execution_mode") != "autonomous_paper_trading":
        raise ValueError("Experiment is outside the paper execution boundary.")
    if experiment.get("status") not in {"running", "paused", "completed"}:
        raise ValueError("Experiment has no eligible operating history.")

    portfolio = read_portfolio(experiment["portfolio_name"])
    if (
        portfolio.get("name") != experiment["portfolio_name"]
        or portfolio.get("portfolio_type") != "paper"
        or type(portfolio.get("id")) is not int
        or portfolio["id"] < 1
    ):
        raise ValueError("Portfolio binding mismatch.")

    policy = read_risk_policy(experiment["risk_policy_name"])
    if policy.get("name") != experiment["risk_policy_name"]:
        raise ValueError("Risk-policy binding mismatch.")

    return {
        "experiment": experiment,
        "portfolio": portfolio,
        "risk_policy": policy,
        "committee_thresholds": read_committee_thresholds(),
    }


def collect_trade_diagnosis(*, experiment_id):
    """Capture a retrospective view without changing any application state.

    Journal rows and configuration are separate reads, not a simultaneous
    database snapshot. Configuration is checked again after journal retrieval.
    """
    if (
        not isinstance(experiment_id, str)
        or not experiment_id.strip()
        or experiment_id != experiment_id.strip()
    ):
        raise ValueError("Supply a trimmed experiment ID.")

    _authorize()
    started_at = datetime.now(timezone.utc).isoformat()
    binding = _binding(experiment_id)
    portfolio_id = binding["portfolio"]["id"]
    result = read_closed_journals(portfolio_id)
    if (
        result.get("status") != "success"
        or result.get("portfolio_id") != portfolio_id
        or result.get("filter") != "closed"
        or not isinstance(result.get("journals"), list)
        or result.get("count") != len(result["journals"])
    ):
        raise ValueError("Journal query binding or structure mismatch.")

    _authorize()
    if _canonical(_binding(experiment_id)) != _canonical(binding):
        raise ValueError("Experiment configuration changed during collection.")

    thresholds = binding["committee_thresholds"]
    diagnosis = build_trade_diagnosis(
        experiment_id=experiment_id,
        strategy_name=binding["experiment"]["strategy_name"],
        portfolio_id=portfolio_id,
        journals=result["journals"],
        queried_at=datetime.now(timezone.utc).isoformat(),
        minimum_profit_factor=thresholds["minimum_profit_factor"],
        minimum_closed_trades=thresholds["minimum_closed_trades"],
        stop_loss_percent=binding["risk_policy"]["stop_loss_percent"],
        query_limit_reached=len(result["journals"]) >= QUERY_LIMIT,
    )
    diagnosis.update({
        "collection_started_at": started_at,
        "configuration": json.loads(_canonical(binding)),
        "configuration_sha256": hashlib.sha256(
            _canonical(binding).encode("utf-8")
        ).hexdigest(),
        "configuration_rechecked": True,
        "simultaneous_snapshot": False,
        "database_writes": False,
        "research_state_writes": False,
    })
    return diagnosis
