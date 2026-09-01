from decimal import Decimal

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import Portfolio


MEAN_REVERSION_PORTFOLIO_NAME = (
    "Jarvis Capital - Mean Reversion V1 Paper"
)


def get_or_create_paper_portfolio(
    *,
    name: str,
    starting_cash_usd: Decimal,
) -> Portfolio:
    normalized_name = name.strip()

    if not normalized_name:
        raise ValueError("name must not be empty.")

    if starting_cash_usd <= Decimal("0"):
        raise ValueError(
            "starting_cash_usd must be greater than zero."
        )

    with SessionLocal() as session:
        portfolio = session.scalar(
            select(Portfolio).where(
                Portfolio.name == normalized_name
            )
        )

        if portfolio is not None:
            if portfolio.portfolio_type != "paper":
                raise ValueError(
                    "Existing portfolio is not a paper portfolio."
                )

            if not portfolio.is_active:
                raise ValueError(
                    "Existing paper portfolio is inactive."
                )

            session.expunge(portfolio)
            return portfolio

        portfolio = Portfolio(
            name=normalized_name,
            portfolio_type="paper",
            cash_balance_usd=starting_cash_usd,
            is_active=True,
        )

        session.add(portfolio)
        session.commit()
        session.refresh(portfolio)
        session.expunge(portfolio)

        return portfolio


def get_or_create_mean_reversion_portfolio() -> Portfolio:
    return get_or_create_paper_portfolio(
        name=MEAN_REVERSION_PORTFOLIO_NAME,
        starting_cash_usd=Decimal("1000.00"),
    )
