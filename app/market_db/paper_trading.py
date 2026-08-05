from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import (
    MarketAsset,
    Portfolio,
    PortfolioPosition,
    PortfolioTransaction,
    PriceObservation,
)
from app.market_db.portfolio_queries import (
    get_or_create_default_portfolio,
)


MONEY_QUANTUM = Decimal("0.00000001")
QUANTITY_QUANTUM = Decimal("0.000000000001")
ZERO = Decimal("0")
ONE = Decimal("1")


class PaperTradingError(ValueError):
    """Raised when a paper-trading operation cannot be completed."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_decimal(
    value: Decimal | int | float | str,
    field_name: str,
) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PaperTradingError(
            f"{field_name} must be a valid number."
        ) from exc

    if not decimal_value.is_finite():
        raise PaperTradingError(
            f"{field_name} must be a finite number."
        )

    return decimal_value


def _positive_money(
    value: Decimal | int | float | str,
    field_name: str = "amount_usd",
) -> Decimal:
    amount = _to_decimal(
        value,
        field_name,
    ).quantize(MONEY_QUANTUM)

    if amount <= ZERO:
        raise PaperTradingError(
            f"{field_name} must be greater than zero."
        )

    return amount


def _positive_quantity(
    value: Decimal | int | float | str,
) -> Decimal:
    quantity = _to_decimal(
        value,
        "quantity",
    ).quantize(QUANTITY_QUANTUM)

    if quantity <= ZERO:
        raise PaperTradingError(
            "quantity must be greater than zero."
        )

    return quantity


def _resolve_portfolio_id(
    portfolio_id: int | None,
) -> int:
    if portfolio_id is not None:
        return portfolio_id

    return get_or_create_default_portfolio().id


def _locked_active_portfolio(
    session: Any,
    portfolio_id: int,
) -> Portfolio:
    portfolio = session.scalar(
        select(Portfolio)
        .where(
            Portfolio.id == portfolio_id,
            Portfolio.is_active.is_(True),
        )
        .with_for_update()
    )

    if portfolio is None:
        raise PaperTradingError(
            f"Active portfolio {portfolio_id} was not found."
        )

    if portfolio.portfolio_type != "paper":
        raise PaperTradingError(
            "Only paper portfolios can use simulated transactions."
        )

    return portfolio


def _resolve_active_asset(
    session: Any,
    symbol: str,
) -> MarketAsset:
    normalized_symbol = symbol.strip().upper()

    if not normalized_symbol:
        raise PaperTradingError(
            "symbol must not be empty."
        )

    asset = session.scalar(
        select(MarketAsset)
        .where(
            MarketAsset.symbol == normalized_symbol,
            MarketAsset.is_active.is_(True),
        )
        .order_by(MarketAsset.id)
        .limit(1)
    )

    if asset is None:
        raise PaperTradingError(
            f"Active asset {normalized_symbol} was not found."
        )

    return asset


def _latest_asset_price(
    session: Any,
    asset_id: int,
) -> PriceObservation:
    observation = session.scalar(
        select(PriceObservation)
        .where(
            PriceObservation.asset_id == asset_id,
        )
        .order_by(
            PriceObservation.observed_at.desc(),
            PriceObservation.id.desc(),
        )
        .limit(1)
    )

    if observation is None:
        raise PaperTradingError(
            "No market price is available for this asset."
        )

    if observation.price_usd <= ZERO:
        raise PaperTradingError(
            "The latest market price is invalid."
        )

    return observation


def _cash_transaction_response(
    transaction: PortfolioTransaction,
    portfolio: Portfolio,
) -> dict[str, Any]:
    return {
        "status": "success",
        "transaction": {
            "id": transaction.id,
            "portfolio_id": transaction.portfolio_id,
            "asset_id": transaction.asset_id,
            "transaction_type": transaction.transaction_type,
            "quantity": float(transaction.quantity),
            "price_usd": float(transaction.price_usd),
            "total_usd": float(transaction.total_usd),
            "fees_usd": float(transaction.fees_usd),
            "realized_gain_loss_usd": None,
            "notes": transaction.notes,
            "created_at": transaction.created_at.isoformat(),
        },
        "cash_balance_usd": float(
            portfolio.cash_balance_usd
        ),
        "processed_at": _utc_now().isoformat(),
    }


def _trade_transaction_response(
    transaction: PortfolioTransaction,
    portfolio: Portfolio,
    asset: MarketAsset,
    position: PortfolioPosition,
    price_observation: PriceObservation,
) -> dict[str, Any]:
    return {
        "status": "success",
        "transaction": {
            "id": transaction.id,
            "portfolio_id": transaction.portfolio_id,
            "asset_id": transaction.asset_id,
            "symbol": asset.symbol,
            "asset_type": asset.asset_type,
            "transaction_type": transaction.transaction_type,
            "quantity": float(transaction.quantity),
            "price_usd": float(transaction.price_usd),
            "total_usd": float(transaction.total_usd),
            "fees_usd": float(transaction.fees_usd),
            "realized_gain_loss_usd": (
                float(transaction.realized_gain_loss_usd)
                if transaction.realized_gain_loss_usd is not None
                else None
            ),
            "notes": transaction.notes,
            "created_at": transaction.created_at.isoformat(),
        },
        "position": {
            "id": position.id,
            "quantity": float(position.quantity),
            "average_cost_usd": float(
                position.average_cost_usd
            ),
        },
        "cash_balance_usd": float(
            portfolio.cash_balance_usd
        ),
        "price": {
            "provider": price_observation.provider,
            "observed_at": (
                price_observation.observed_at.isoformat()
            ),
        },
        "processed_at": _utc_now().isoformat(),
    }


def deposit_cash(
    amount_usd: Decimal | int | float | str,
    portfolio_id: int | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    amount = _positive_money(amount_usd)
    resolved_portfolio_id = _resolve_portfolio_id(
        portfolio_id
    )

    with SessionLocal.begin() as session:
        portfolio = _locked_active_portfolio(
            session=session,
            portfolio_id=resolved_portfolio_id,
        )

        portfolio.cash_balance_usd = (
            portfolio.cash_balance_usd + amount
        )

        transaction = PortfolioTransaction(
            portfolio_id=portfolio.id,
            asset_id=None,
            transaction_type="deposit",
            quantity=amount,
            price_usd=ONE,
            total_usd=amount,
            fees_usd=ZERO,
            realized_gain_loss_usd=None,
            notes=notes,
        )

        session.add(transaction)
        session.flush()
        session.refresh(transaction)

        response = _cash_transaction_response(
            transaction=transaction,
            portfolio=portfolio,
        )

    return response


def withdraw_cash(
    amount_usd: Decimal | int | float | str,
    portfolio_id: int | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    amount = _positive_money(amount_usd)
    resolved_portfolio_id = _resolve_portfolio_id(
        portfolio_id
    )

    with SessionLocal.begin() as session:
        portfolio = _locked_active_portfolio(
            session=session,
            portfolio_id=resolved_portfolio_id,
        )

        if portfolio.cash_balance_usd < amount:
            raise PaperTradingError(
                "Insufficient cash balance. "
                f"Available: ${portfolio.cash_balance_usd:.2f}; "
                f"requested: ${amount:.2f}."
            )

        portfolio.cash_balance_usd = (
            portfolio.cash_balance_usd - amount
        )

        transaction = PortfolioTransaction(
            portfolio_id=portfolio.id,
            asset_id=None,
            transaction_type="withdrawal",
            quantity=amount,
            price_usd=ONE,
            total_usd=amount,
            fees_usd=ZERO,
            realized_gain_loss_usd=None,
            notes=notes,
        )

        session.add(transaction)
        session.flush()
        session.refresh(transaction)

        response = _cash_transaction_response(
            transaction=transaction,
            portfolio=portfolio,
        )

    return response


def buy_asset(
    symbol: str,
    quantity: Decimal | int | float | str,
    portfolio_id: int | None = None,
    fees_usd: Decimal | int | float | str = ZERO,
    notes: str | None = None,
) -> dict[str, Any]:
    trade_quantity = _positive_quantity(quantity)
    fees = _to_decimal(
        fees_usd,
        "fees_usd",
    ).quantize(MONEY_QUANTUM)

    if fees < ZERO:
        raise PaperTradingError(
            "fees_usd must not be negative."
        )

    resolved_portfolio_id = _resolve_portfolio_id(
        portfolio_id
    )

    with SessionLocal.begin() as session:
        portfolio = _locked_active_portfolio(
            session=session,
            portfolio_id=resolved_portfolio_id,
        )
        asset = _resolve_active_asset(
            session=session,
            symbol=symbol,
        )
        price_observation = _latest_asset_price(
            session=session,
            asset_id=asset.id,
        )

        execution_price = (
            price_observation.price_usd.quantize(
                MONEY_QUANTUM
            )
        )
        trade_total = (
            trade_quantity * execution_price
        ).quantize(MONEY_QUANTUM)
        cash_required = trade_total + fees

        if portfolio.cash_balance_usd < cash_required:
            raise PaperTradingError(
                "Insufficient cash balance. "
                f"Available: ${portfolio.cash_balance_usd:.2f}; "
                f"required: ${cash_required:.2f}."
            )

        position = session.scalar(
            select(PortfolioPosition)
            .where(
                PortfolioPosition.portfolio_id
                == portfolio.id,
                PortfolioPosition.asset_id == asset.id,
            )
            .with_for_update()
        )

        if position is None:
            position = PortfolioPosition(
                portfolio_id=portfolio.id,
                asset_id=asset.id,
                quantity=ZERO,
                average_cost_usd=ZERO,
            )
            session.add(position)
            session.flush()

        existing_quantity = position.quantity
        existing_cost_basis = (
            existing_quantity * position.average_cost_usd
        )
        added_cost_basis = trade_total + fees
        updated_quantity = (
            existing_quantity + trade_quantity
        )

        position.quantity = updated_quantity
        position.average_cost_usd = (
            (
                existing_cost_basis + added_cost_basis
            )
            / updated_quantity
        ).quantize(MONEY_QUANTUM)

        portfolio.cash_balance_usd = (
            portfolio.cash_balance_usd - cash_required
        )

        transaction = PortfolioTransaction(
            portfolio_id=portfolio.id,
            asset_id=asset.id,
            transaction_type="buy",
            quantity=trade_quantity,
            price_usd=execution_price,
            total_usd=trade_total,
            fees_usd=fees,
            realized_gain_loss_usd=None,
            notes=notes,
        )

        session.add(transaction)
        session.flush()
        session.refresh(transaction)
        session.refresh(position)

        response = _trade_transaction_response(
            transaction=transaction,
            portfolio=portfolio,
            asset=asset,
            position=position,
            price_observation=price_observation,
        )

    return response


def sell_asset(
    symbol: str,
    quantity: Decimal | int | float | str,
    portfolio_id: int | None = None,
    fees_usd: Decimal | int | float | str = ZERO,
    notes: str | None = None,
) -> dict[str, Any]:
    trade_quantity = _positive_quantity(quantity)
    fees = _to_decimal(
        fees_usd,
        "fees_usd",
    ).quantize(MONEY_QUANTUM)

    if fees < ZERO:
        raise PaperTradingError(
            "fees_usd must not be negative."
        )

    resolved_portfolio_id = _resolve_portfolio_id(
        portfolio_id
    )

    with SessionLocal.begin() as session:
        portfolio = _locked_active_portfolio(
            session=session,
            portfolio_id=resolved_portfolio_id,
        )
        asset = _resolve_active_asset(
            session=session,
            symbol=symbol,
        )
        price_observation = _latest_asset_price(
            session=session,
            asset_id=asset.id,
        )

        position = session.scalar(
            select(PortfolioPosition)
            .where(
                PortfolioPosition.portfolio_id
                == portfolio.id,
                PortfolioPosition.asset_id == asset.id,
            )
            .with_for_update()
        )

        if position is None or position.quantity <= ZERO:
            raise PaperTradingError(
                f"No open {asset.symbol} position was found."
            )

        if position.quantity < trade_quantity:
            raise PaperTradingError(
                "Insufficient position quantity. "
                f"Available: {position.quantity}; "
                f"requested: {trade_quantity}."
            )

        execution_price = (
            price_observation.price_usd.quantize(
                MONEY_QUANTUM
            )
        )
        trade_total = (
            trade_quantity * execution_price
        ).quantize(MONEY_QUANTUM)
        net_proceeds = trade_total - fees

        if net_proceeds < ZERO:
            raise PaperTradingError(
                "fees_usd cannot exceed the sale proceeds."
            )

        sold_cost_basis = (
            trade_quantity * position.average_cost_usd
        ).quantize(MONEY_QUANTUM)
        realized_gain_loss = (
            net_proceeds - sold_cost_basis
        ).quantize(MONEY_QUANTUM)

        position.quantity = (
            position.quantity - trade_quantity
        ).quantize(QUANTITY_QUANTUM)

        if position.quantity == ZERO:
            position.average_cost_usd = ZERO

        portfolio.cash_balance_usd = (
            portfolio.cash_balance_usd + net_proceeds
        )

        transaction = PortfolioTransaction(
            portfolio_id=portfolio.id,
            asset_id=asset.id,
            transaction_type="sell",
            quantity=trade_quantity,
            price_usd=execution_price,
            total_usd=trade_total,
            fees_usd=fees,
            realized_gain_loss_usd=realized_gain_loss,
            notes=notes,
        )

        session.add(transaction)
        session.flush()
        session.refresh(transaction)
        session.refresh(position)

        response = _trade_transaction_response(
            transaction=transaction,
            portfolio=portfolio,
            asset=asset,
            position=position,
            price_observation=price_observation,
        )

    return response
