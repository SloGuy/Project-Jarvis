from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import (
    MarketAsset,
    Portfolio,
    PortfolioPosition,
    PriceObservation,
)


DEFAULT_PORTFOLIO_NAME = "Primary Portfolio"
DEFAULT_STARTING_CASH = Decimal("100000.00")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None

    return float(value)


def get_or_create_default_portfolio() -> Portfolio:
    with SessionLocal() as session:
        portfolio = session.scalar(
            select(Portfolio)
            .where(Portfolio.is_active.is_(True))
            .order_by(Portfolio.id)
            .limit(1)
        )

        if portfolio is None:
            portfolio = Portfolio(
                name=DEFAULT_PORTFOLIO_NAME,
                portfolio_type="paper",
                cash_balance_usd=DEFAULT_STARTING_CASH,
                is_active=True,
            )

            session.add(portfolio)
            session.commit()
            session.refresh(portfolio)

        session.expunge(portfolio)

    return portfolio


def _latest_price_observation(
    session: Any,
    asset_id: int,
) -> PriceObservation | None:
    return session.scalar(
        select(PriceObservation)
        .where(PriceObservation.asset_id == asset_id)
        .order_by(PriceObservation.observed_at.desc())
        .limit(1)
    )


def get_portfolio_summary(
    portfolio_id: int | None = None,
) -> dict[str, Any]:
    if portfolio_id is None:
        default_portfolio = get_or_create_default_portfolio()
        portfolio_id = default_portfolio.id

    with SessionLocal() as session:
        portfolio = session.scalar(
            select(Portfolio).where(
                Portfolio.id == portfolio_id,
                Portfolio.is_active.is_(True),
            )
        )

        if portfolio is None:
            return {
                "status": "not_found",
                "portfolio_id": portfolio_id,
                "generated_at": _utc_now().isoformat(),
                "summary": "Portfolio was not found.",
                "positions": [],
            }

        rows = session.execute(
            select(
                PortfolioPosition,
                MarketAsset,
            )
            .join(
                MarketAsset,
                MarketAsset.id == PortfolioPosition.asset_id,
            )
            .where(
                PortfolioPosition.portfolio_id == portfolio.id,
                PortfolioPosition.quantity > 0,
            )
            .order_by(MarketAsset.symbol)
        ).all()

        positions = []
        invested_cost_usd = Decimal("0")
        market_value_usd = Decimal("0")
        unrealized_gain_loss_usd = Decimal("0")

        for position, asset in rows:
            latest = _latest_price_observation(
                session=session,
                asset_id=asset.id,
            )

            quantity = position.quantity
            average_cost = position.average_cost_usd
            cost_basis = quantity * average_cost

            latest_price = (
                latest.price_usd
                if latest is not None
                else None
            )

            position_market_value = (
                quantity * latest_price
                if latest_price is not None
                else None
            )

            position_gain_loss = (
                position_market_value - cost_basis
                if position_market_value is not None
                else None
            )

            position_gain_loss_percent = None

            if (
                position_gain_loss is not None
                and cost_basis != 0
            ):
                position_gain_loss_percent = (
                    position_gain_loss
                    / cost_basis
                    * Decimal("100")
                )

            invested_cost_usd += cost_basis

            if position_market_value is not None:
                market_value_usd += position_market_value

            if position_gain_loss is not None:
                unrealized_gain_loss_usd += position_gain_loss

            positions.append(
                {
                    "position_id": position.id,
                    "asset_id": asset.id,
                    "symbol": asset.symbol,
                    "name": asset.name,
                    "asset_type": asset.asset_type,
                    "quantity": _to_float(quantity),
                    "average_cost_usd": _to_float(
                        average_cost
                    ),
                    "cost_basis_usd": _to_float(
                        cost_basis
                    ),
                    "latest_price_usd": _to_float(
                        latest_price
                    ),
                    "market_value_usd": _to_float(
                        position_market_value
                    ),
                    "unrealized_gain_loss_usd": _to_float(
                        position_gain_loss
                    ),
                    "unrealized_gain_loss_percent": _to_float(
                        position_gain_loss_percent
                    ),
                    "price_provider": (
                        latest.provider
                        if latest is not None
                        else None
                    ),
                    "price_observed_at": (
                        latest.observed_at.isoformat()
                        if latest is not None
                        else None
                    ),
                    "created_at": (
                        position.created_at.isoformat()
                    ),
                    "updated_at": (
                        position.updated_at.isoformat()
                    ),
                }
            )

        cash_balance = portfolio.cash_balance_usd
        total_value_usd = cash_balance + market_value_usd

        cash_allocation_percent = None
        invested_allocation_percent = None

        if total_value_usd != 0:
            cash_allocation_percent = (
                cash_balance
                / total_value_usd
                * Decimal("100")
            )
            invested_allocation_percent = (
                market_value_usd
                / total_value_usd
                * Decimal("100")
            )

        for position in positions:
            position_value = position.get(
                "market_value_usd"
            )

            allocation_percent = None

            if (
                position_value is not None
                and total_value_usd != 0
            ):
                allocation_percent = (
                    Decimal(str(position_value))
                    / total_value_usd
                    * Decimal("100")
                )

            position["allocation_percent"] = _to_float(
                allocation_percent
            )

        return {
            "status": "success",
            "generated_at": _utc_now().isoformat(),
            "portfolio": {
                "id": portfolio.id,
                "name": portfolio.name,
                "portfolio_type": portfolio.portfolio_type,
                "is_active": portfolio.is_active,
                "created_at": portfolio.created_at.isoformat(),
                "updated_at": portfolio.updated_at.isoformat(),
            },
            "cash_balance_usd": _to_float(
                cash_balance
            ),
            "invested_cost_usd": _to_float(
                invested_cost_usd
            ),
            "market_value_usd": _to_float(
                market_value_usd
            ),
            "unrealized_gain_loss_usd": _to_float(
                unrealized_gain_loss_usd
            ),
            "total_value_usd": _to_float(
                total_value_usd
            ),
            "cash_allocation_percent": _to_float(
                cash_allocation_percent
            ),
            "invested_allocation_percent": _to_float(
                invested_allocation_percent
            ),
            "position_count": len(positions),
            "positions": positions,
        }
