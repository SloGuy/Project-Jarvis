from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal

from app.autonomous_trading.policy import INITIAL_1000_POLICY
from app.capital.cycle_health import get_cycle_health
from app.capital.experiment_registry import list_experiments
from app.capital.policies import (
    MEAN_REVERSION_1000_POLICY,
    MEAN_REVERSION_V2_1000_POLICY,
    VOLATILITY_BREAKOUT_1000_POLICY,
)


POLICIES = {
    policy.name: policy
    for policy in (
        INITIAL_1000_POLICY,
        MEAN_REVERSION_1000_POLICY,
        MEAN_REVERSION_V2_1000_POLICY,
        VOLATILITY_BREAKOUT_1000_POLICY,
    )
}

RISK_MONITORED_STRATEGIES = {
    "mean_reversion_v1",
    "mean_reversion_v2",
}


def get_experiment_oversight():
    experiments = []

    for experiment in list_experiments():
        policy = POLICIES.get(experiment.risk_policy_name)
        modes = ["regular"]
        if experiment.strategy_name in RISK_MONITORED_STRATEGIES:
            modes.append("risk_only")

        cycles = []
        for mode in modes:
            try:
                health = get_cycle_health(
                    experiment_id=experiment.experiment_id,
                    mode=mode,
                )
            except Exception:
                health = {
                    "mode": mode,
                    "state": "unavailable",
                    "error": "Cycle history could not be read.",
                }
            cycles.append(health)

        policy_settings = None
        if policy is not None:
            policy_settings = {
                key: float(value) if isinstance(value, Decimal)
                else value
                for key, value in asdict(policy).items()
            }

        experiments.append({
            **experiment.to_dict(),
            "policy_status": (
                "available" if policy is not None else "not_found"
            ),
            "policy_settings": policy_settings,
            "cycles": cycles,
            "journal_health": _journal_health(experiment),
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "live_capital_enabled": False,
        "experiments": experiments,
    }


def _journal_health(experiment):
    from sqlalchemy import select
    from app.capital.journal_health import get_journal_health
    from app.market_db.database import SessionLocal
    from app.market_db.models import Portfolio

    try:
        with SessionLocal() as session:
            ids = session.scalars(
                select(Portfolio.id).where(
                    Portfolio.name == experiment.portfolio_name,
                    Portfolio.is_active.is_(True),
                )
            ).all()
        if len(ids) != 1:
            return {
                "state": "unavailable",
                "error": f"Expected one active portfolio; found {len(ids)}.",
            }
        return get_journal_health(portfolio_id=ids[0])
    except Exception:
        return {
            "state": "unavailable",
            "error": "Journal integrity could not be checked.",
        }
