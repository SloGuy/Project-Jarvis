import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.capital.signal_replay import ReplayConfirmation, replay_entry_signals
from app.autonomous_trading.strategy import StrategyAction as A
from app.capital.mean_reversion_math import calculate_mean_reversion_snapshot

NOW = datetime(2026, 9, 4, 18, tzinfo=timezone.utc)


class ReplayTests(unittest.TestCase):
    def test_confirmation_timestamps_and_reset(self):
        state = ReplayConfirmation()
        def update(action, minutes):
            return state.update(
                symbol="TEST", strategy_name="test",
                action=action, observation_at=NOW + timedelta(minutes=minutes),
            )
        self.assertEqual(update(A.BUY, 0).confirmation_count, 1)
        self.assertEqual(update(A.BUY, 0).confirmation_count, 1)
        self.assertEqual(update(A.BUY, -1).confirmation_count, 1)
        self.assertFalse(update(A.BUY, 1).confirmed)
        self.assertTrue(update(A.BUY, 2).confirmed)
        self.assertEqual(update(A.HOLD, 3).confirmation_count, 0)
        self.assertFalse(update(A.BUY, 4).confirmed)

    def test_replay_is_repeatable_and_never_uses_database_confirmation(self):
        def window(session, **kwargs):
            at = kwargs["decision_at"] - timedelta(seconds=1)
            observations = [
                {"price_usd": 90 if i == 0 else 100,
                 "observed_at": at.isoformat()}
                for i in range(20)
            ]
            return {
                "snapshot": calculate_mean_reversion_snapshot(
                    symbol="TEST", observations=observations
                ),
                "observation_ids": list(range(20)),
            }

        with patch(
            "app.capital.signal_replay.load_historical_snapshot",
            side_effect=window,
        ), patch(
            "app.autonomous_trading.mean_reversion_v2_strategy."
            "update_signal_confirmation",
            side_effect=AssertionError("Live confirmation called"),
        ):
            arguments = dict(
                asset_id=1, provider="Finnhub", start=NOW,
                end=NOW + timedelta(minutes=15),
            )
            first = replay_entry_signals(None, **arguments)
            second = replay_entry_signals(None, **arguments)
        self.assertEqual(first, second)
        self.assertEqual(
            [row["action"] for row in first["records"]],
            ["hold", "hold", "buy"],
        )
        self.assertEqual(first["decision_count"], 3)


if __name__ == "__main__":
    unittest.main()
