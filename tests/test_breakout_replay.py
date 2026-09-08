from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import unittest
from unittest.mock import patch

from app.autonomous_trading.volatility_breakout_strategy import (
    calculate_volatility_breakout_snapshot,
    evaluate_volatility_breakout_strategy,
    VolatilityBreakoutSnapshot,
)
from app.autonomous_trading.strategy import PositionContext
from app.capital.signal_replay import ReplayConfirmation

NOW = datetime(2026, 9, 4, 14, tzinfo=timezone.utc)


def context(held=False):
    return PositionContext(
        symbol="SPY", quantity=D("1") if held else D("0"),
        average_cost_usd=D("100") if held else D("0"),
        market_value_usd=D("100") if held else D("0"),
        allocation_percent=D("10") if held else D("0"),
        unrealized_gain_loss_usd=D("0"),
        unrealized_gain_loss_percent=D("0"),
        opened_at=NOW - timedelta(hours=1) if held else None,
    )


def snapshot(at, price="102"):
    return VolatilityBreakoutSnapshot(
        symbol="SPY", observation_at=at,
        latest_price_usd=D(price),
        compressed_range_high_usd=D("100"),
        compressed_range_low_usd=D("99.9"),
        compression_ratio=D("0.1"),
        breakout_percent=D("2"),
        expansion_ratio=D("3"),
        exit_average_usd=D("100"),
        observation_count=60, usable=True, reason=None,
    )


class BreakoutReplayTests(unittest.TestCase):
    def test_arithmetic_and_no_history_access(self):
        prices = [102] + [100, 100.1] * 10 + [98, 102] * 10
        observations = [
            {
                "price_usd": price,
                "observed_at": (NOW - timedelta(minutes=index)).isoformat(),
            }
            for index, price in enumerate(prices)
        ]
        with patch(
            "app.autonomous_trading.volatility_breakout_strategy.get_market_history",
            side_effect=AssertionError("Unexpected market history access"),
        ):
            result = calculate_volatility_breakout_snapshot(
                symbol="SPY", observations=observations
            )
        self.assertTrue(result.usable)
        self.assertEqual(result.observation_count, 41)
        self.assertEqual(result.latest_price_usd, D("102"))
        self.assertEqual(result.compressed_range_high_usd, D("100.1"))
        self.assertLess(result.compression_ratio, D("0.60"))

    def test_empty_and_constant_history(self):
        result = calculate_volatility_breakout_snapshot(
            symbol="SPY", observations=[]
        )
        self.assertFalse(result.usable)
        observations = [
            {"price_usd": 100, "observed_at": NOW.isoformat()}
            for _ in range(60)
        ]
        result = calculate_volatility_breakout_snapshot(
            symbol="SPY", observations=observations
        )
        self.assertFalse(result.usable)
        self.assertEqual(result.reason, "Baseline price range is zero.")

    def test_confirmed_entry_and_exit_without_shared_state(self):
        confirmation = ReplayConfirmation()
        with patch(
            "app.autonomous_trading.volatility_breakout_strategy.update_signal_confirmation",
            side_effect=AssertionError("Paper confirmation must not be used"),
        ):
            actions = []
            for minute in (0, 5, 10):
                result = evaluate_volatility_breakout_strategy(
                    symbol="SPY", position_context=context(),
                    snapshot=snapshot(NOW + timedelta(minutes=minute)),
                    confirmation_handler=confirmation.update,
                )
                actions.append(result.action.value)
            self.assertEqual(actions, ["hold", "hold", "buy"])
            actions = []
            for minute in (15, 20, 25):
                result = evaluate_volatility_breakout_strategy(
                    symbol="SPY", position_context=context(held=True),
                    snapshot=snapshot(NOW + timedelta(minutes=minute), price="99"),
                    confirmation_handler=confirmation.update,
                )
                actions.append(result.action.value)
            self.assertEqual(actions, ["hold", "hold", "sell"])


if __name__ == "__main__":
    unittest.main()
