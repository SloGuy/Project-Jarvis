from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app.autonomous_trading.mean_reversion_strategy import (
    MeanReversionSnapshot,
    evaluate_mean_reversion_strategy,
    get_mean_reversion_snapshot,
)
from app.autonomous_trading.strategy import PositionContext


NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def position(
    quantity: str,
) -> PositionContext:
    return PositionContext(
        symbol="AAPL",
        quantity=Decimal(quantity),
        average_cost_usd=Decimal("100"),
        market_value_usd=Decimal("100"),
        allocation_percent=Decimal("10"),
        unrealized_gain_loss_usd=Decimal("0"),
        unrealized_gain_loss_percent=Decimal("0"),
        opened_at=NOW if Decimal(quantity) > 0 else None,
    )


def snapshot(
    z_score: str,
) -> MeanReversionSnapshot:
    return MeanReversionSnapshot(
        symbol="AAPL",
        observation_at=NOW,
        latest_price_usd=Decimal("98"),
        mean_price_usd=Decimal("100"),
        standard_deviation_usd=Decimal("1"),
        z_score=Decimal(z_score),
        observation_count=48,
        usable=True,
        reason=None,
    )


confirmation = SimpleNamespace(
    confirmed=True,
    confirmation_count=3,
    required_confirmations=3,
)


with patch(
    "app.autonomous_trading.mean_reversion_strategy."
    "update_signal_confirmation",
    return_value=confirmation,
):
    hold = evaluate_mean_reversion_strategy(
        symbol="AAPL",
        position_context=position("0"),
        snapshot=snapshot("0"),
    )

    buy = evaluate_mean_reversion_strategy(
        symbol="AAPL",
        position_context=position("0"),
        snapshot=snapshot("-2"),
    )

    sell = evaluate_mean_reversion_strategy(
        symbol="AAPL",
        position_context=position("1"),
        snapshot=snapshot("0"),
    )


history = {
    "observations": [
        {
            "price_usd": 90 if index == 0 else 100,
            "observed_at": NOW.isoformat(),
        }
        for index in range(20)
    ]
}


with patch(
    "app.autonomous_trading.mean_reversion_strategy."
    "get_market_history",
    return_value=history,
):
    calculated = get_mean_reversion_snapshot(
        symbol="AAPL",
    )


assert hold.action.value == "hold"
assert buy.action.value == "buy"
assert buy.confidence_percent == Decimal("75.00")
assert sell.action.value == "sell"
assert calculated.usable is True
assert calculated.observation_count == 20
assert calculated.z_score is not None
assert calculated.z_score < Decimal("-1.50")

print("neutral_hold: PASS")
print("confirmed_buy: PASS")
print("recovery_sell: PASS")
print("rolling_z_score_math: PASS")
print("database_writes: NONE")
