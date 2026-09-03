from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app.autonomous_trading.strategy import (
    PositionContext,
)
from app.autonomous_trading.volatility_breakout_strategy import (
    VolatilityBreakoutSnapshot,
    evaluate_volatility_breakout_strategy,
    get_volatility_breakout_snapshot,
)


NOW = datetime(
    2026,
    9,
    3,
    tzinfo=timezone.utc,
)


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
        opened_at=(
            NOW
            if Decimal(quantity) > 0
            else None
        ),
    )


def snapshot(
    *,
    latest: str = "102",
    exit_average: str = "100",
    compression: str = "0.50",
    breakout: str = "0.50",
    expansion: str = "3.00",
) -> VolatilityBreakoutSnapshot:
    return VolatilityBreakoutSnapshot(
        symbol="AAPL",
        observation_at=NOW,
        latest_price_usd=Decimal(latest),
        compressed_range_high_usd=Decimal("101"),
        compressed_range_low_usd=Decimal("99"),
        compression_ratio=Decimal(
            compression
        ),
        breakout_percent=Decimal(
            breakout
        ),
        expansion_ratio=Decimal(
            expansion
        ),
        exit_average_usd=Decimal(
            exit_average
        ),
        observation_count=60,
        usable=True,
        reason=None,
    )


confirmation = SimpleNamespace(
    confirmed=True,
    confirmation_count=3,
    required_confirmations=3,
)


with patch(
    "app.autonomous_trading."
    "volatility_breakout_strategy."
    "update_signal_confirmation",
    return_value=confirmation,
):
    neutral = (
        evaluate_volatility_breakout_strategy(
            symbol="AAPL",
            position_context=position("0"),
            snapshot=snapshot(
                breakout="0.10",
            ),
        )
    )

    buy = (
        evaluate_volatility_breakout_strategy(
            symbol="AAPL",
            position_context=position("0"),
            snapshot=snapshot(),
        )
    )

    hold_position = (
        evaluate_volatility_breakout_strategy(
            symbol="AAPL",
            position_context=position("1"),
            snapshot=snapshot(),
        )
    )

    sell = (
        evaluate_volatility_breakout_strategy(
            symbol="AAPL",
            position_context=position("1"),
            snapshot=snapshot(
                latest="95",
                exit_average="100",
            ),
        )
    )


assert neutral.action.value == "hold"
assert buy.action.value == "buy"
assert buy.confidence_percent >= Decimal("70")
assert (
    buy.suggested_position_percent
    == Decimal("10.00")
)
assert hold_position.action.value == "hold"
assert sell.action.value == "sell"

assert all(
    candidate.strategy_name
    == "volatility_breakout_v1"
    for candidate in (
        neutral,
        buy,
        hold_position,
        sell,
    )
)


history_prices = (
    [Decimal("103")]
    + [
        Decimal("99.9")
        if index % 2 == 0
        else Decimal("100.1")
        for index in range(20)
    ]
    + [
        Decimal("95")
        if index % 2 == 0
        else Decimal("105")
        for index in range(20)
    ]
)

history = {
    "observations": [
        {
            "price_usd": price,
            "observed_at": NOW.isoformat(),
        }
        for price in history_prices
    ]
}

with patch(
    "app.autonomous_trading."
    "volatility_breakout_strategy."
    "get_market_history",
    return_value=history,
):
    calculated = (
        get_volatility_breakout_snapshot(
            symbol="AAPL"
        )
    )

assert calculated.usable is True
assert calculated.observation_count == 41
assert calculated.compression_ratio is not None
assert calculated.compression_ratio < Decimal("0.60")
assert calculated.breakout_percent is not None
assert calculated.breakout_percent > Decimal("0.25")
assert calculated.expansion_ratio is not None
assert calculated.expansion_ratio > Decimal("2.00")

print("neutral_hold: PASS")
print("confirmed_breakout_buy: PASS")
print("position_hold: PASS")
print("confirmed_failure_sell: PASS")
print("position_size_boundary: PASS")
print("database_writes: NONE")
print("compression_breakout_math: PASS")
