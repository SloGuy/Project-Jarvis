from typing import Any

from sqlalchemy import select

from app.capital.experiment_registry import (
    list_experiments,
)
from app.market_db.database import SessionLocal
from app.market_db.models import Portfolio
from app.market_db.portfolio_queries import (
    get_portfolio_summary,
)


def get_strategy_portfolios() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for experiment in list_experiments():
        with SessionLocal() as session:
            portfolio = session.scalar(
                select(Portfolio).where(
                    Portfolio.name
                    == experiment.portfolio_name,
                    Portfolio.is_active.is_(True),
                )
            )

            if portfolio is None:
                results.append(
                    {
                        "experiment_id": (
                            experiment.experiment_id
                        ),
                        "strategy_name": (
                            experiment.strategy_name
                        ),
                        "experiment_status": (
                            experiment.status.value
                        ),
                        "execution_mode": (
                            experiment.execution_mode
                        ),
                        "portfolio_status": "not_found",
                    }
                )
                continue

            portfolio_id = portfolio.id
            portfolio_name = portfolio.name

        summary = get_portfolio_summary(
            portfolio_id=portfolio_id,
            transaction_limit=5,
        )

        current_value = float(
            summary.get("total_value_usd")
            or 0
        )

        starting_capital = float(
            experiment.starting_capital_usd
        )

        total_return_percent = 0.0

        if starting_capital > 0:
            total_return_percent = (
                (
                    current_value
                    - starting_capital
                )
                / starting_capital
                * 100
            )

        results.append(
            {
                "experiment_id": experiment.experiment_id,
                "strategy_name": experiment.strategy_name,
                "experiment_status": (
                    experiment.status.value
                ),
                "execution_mode": experiment.execution_mode,
                "portfolio_status": summary.get(
                    "status"
                ),
                "portfolio_id": portfolio_id,
                "portfolio_name": portfolio_name,
                "starting_capital_usd": starting_capital,
                "current_value_usd": current_value,
                "cash_balance_usd": float(
                    summary.get("cash_balance_usd")
                    or 0
                ),
                "market_value_usd": float(
                    summary.get("market_value_usd")
                    or 0
                ),
                "total_gain_loss_usd": float(
                    summary.get("total_gain_loss_usd")
                    or 0
                ),
                "total_return_percent": (
                    total_return_percent
                ),
                "open_position_count": int(
                    summary.get("position_count")
                    or 0
                ),
                "transaction_count": int(
                    summary.get("transaction_count")
                    or 0
                ),
            }
        )

    return results
