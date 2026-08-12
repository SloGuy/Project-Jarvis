from decimal import Decimal

from sqlalchemy import select

from app.autonomous_trading.proposals import (
    TradeAction,
    TradeProposal,
)
from app.market_db.database import SessionLocal
from app.market_db.market_pricing import resolve_market_price
from app.market_db.models import MarketAsset


class ProposalBuilderError(ValueError):
    """Raised when a trade proposal cannot be constructed."""


def build_trade_proposal(
    *,
    symbol: str,
    action: TradeAction,
    quantity: Decimal,
    confidence_percent: Decimal,
    rationale: str,
    strategy_name: str,
) -> TradeProposal:
    """
    Build a trade proposal using Jarvis's current market price.

    This function reads market data only. It does not approve
    or execute trades.
    """

    normalized_symbol = symbol.strip().upper()

    if not normalized_symbol:
        raise ProposalBuilderError(
            "symbol must not be empty."
        )

    with SessionLocal() as session:
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
            raise ProposalBuilderError(
                f"Active asset {normalized_symbol} was not found."
            )

        market_price = resolve_market_price(
            session=session,
            asset=asset,
        )

    return TradeProposal.create(
        symbol=normalized_symbol,
        action=action,
        quantity=quantity,
        reference_price_usd=market_price["price_usd"],
        price_observed_at=market_price["observed_at"],
        confidence_percent=confidence_percent,
        rationale=rationale,
        strategy_name=strategy_name,
    )
