from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TradeAction(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class TradeProposal:
    """
    A proposed trade produced by a strategy.

    Creating a proposal does not approve or execute a trade.
    Every proposal must pass through the risk governor before
    it can ever reach the paper trading engine.
    """

    symbol: str
    action: TradeAction
    quantity: Decimal

    reference_price_usd: Decimal
    price_observed_at: datetime

    confidence_percent: Decimal
    rationale: str

    strategy_name: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        symbol: str,
        action: TradeAction,
        quantity: Decimal,
        reference_price_usd: Decimal,
        price_observed_at: datetime,
        confidence_percent: Decimal,
        rationale: str,
        strategy_name: str,
    ) -> "TradeProposal":
        return cls(
            symbol=symbol.upper().strip(),
            action=action,
            quantity=quantity,
            reference_price_usd=reference_price_usd,
            price_observed_at=price_observed_at,
            confidence_percent=confidence_percent,
            rationale=rationale.strip(),
            strategy_name=strategy_name.strip(),
            created_at=utc_now(),
        )
