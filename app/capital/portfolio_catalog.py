from sqlalchemy import select

from app.capital.experiment_registry import list_experiments
from app.market_db.database import SessionLocal
from app.market_db.models import Portfolio


def get_portfolio_catalog():
    experiments = list_experiments()
    entries = []

    with SessionLocal() as session:
        portfolios = session.scalars(
            select(Portfolio).order_by(Portfolio.id)
        ).all()

        for portfolio in portfolios:
            matches = [
                experiment
                for experiment in experiments
                if experiment.portfolio_name == portfolio.name
            ]

            experiment = matches[0] if len(matches) == 1 else None
            entries.append({
                "portfolio_id": portfolio.id,
                "portfolio_name": portfolio.name,
                "portfolio_active": portfolio.is_active,
                "experiment_id": (
                    experiment.experiment_id if experiment else None
                ),
                "experiment_name": (
                    experiment.name if experiment else None
                ),
                "strategy_name": (
                    experiment.strategy_name if experiment else None
                ),
                "experiment_status": (
                    experiment.status.value if experiment else None
                ),
                "running_experiment": bool(
                    experiment
                    and experiment.status.value == "running"
                    and portfolio.is_active
                ),
                "registration_status": (
                    "matched" if experiment
                    else "ambiguous" if matches
                    else "unregistered"
                ),
            })

    return {
        "status": "success",
        "portfolios": entries,
    }
