from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import runpy
import unittest
from unittest.mock import patch

from app.capital.shadow_coordinator import ShadowCoordinator
from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY as POLICY

mean_fixture = runpy.run_path("tests/test_position_simulation.py")["snapshot"]
breakout_fixture = runpy.run_path("tests/test_breakout_replay.py")["snapshot"]
NOW = datetime(2026, 9, 4, 14, tzinfo=timezone.utc)


class CoordinatorTests(unittest.TestCase):
    def make(self, costs="0"):
        return ShadowCoordinator(
            policy=POLICY,
            strategy_caps={"mean_reversion_v2": "15", "volatility_breakout_v1": "15"},
            fee_bps=costs, slippage_bps=costs,
        )

    def tick(self, coordinator, minute, price="98", reverse=False, risk_only=False):
        at = NOW + timedelta(minutes=minute)
        snapshots = {
            ("mean_reversion_v2", "SPY"): mean_fixture(at, price),
            ("volatility_breakout_v1", "SPY"): breakout_fixture(
                at - timedelta(seconds=1), price
            ),
        }
        if reverse:
            snapshots = dict(reversed(list(snapshots.items())))
        return coordinator.step(
            decision_at=at, snapshots=snapshots,
            quotes={"SPY": (D(price), at - timedelta(seconds=1))},
            risk_only=risk_only,
        )

    def test_real_evaluators_share_one_portfolio(self):
        coordinator = self.make()
        with (
            patch(
                "app.autonomous_trading.mean_reversion_v2_strategy.update_signal_confirmation",
                side_effect=AssertionError("Paper confirmation access"),
            ),
            patch(
                "app.autonomous_trading.volatility_breakout_strategy.update_signal_confirmation",
                side_effect=AssertionError("Paper confirmation access"),
            ),
        ):
            for minute in (0, 5, 10):
                result = self.tick(coordinator, minute)
        self.assertEqual(len(coordinator.ledger.positions), 2)
        self.assertEqual(result["account"]["position_count"], 1)
        self.assertEqual(
            {event["strategy"] for event in result["events"]},
            {"mean_reversion_v2", "volatility_breakout_v1"},
        )
        self.assertTrue(all(event["executed"] for event in result["events"]))
        self.assertGreaterEqual(coordinator.ledger.cash, D("800"))
        result = self.tick(coordinator, 15, price="100")
        self.assertNotIn(("mean_reversion_v2", "SPY"), coordinator.ledger.positions)
        self.assertIn(("volatility_breakout_v1", "SPY"), coordinator.ledger.positions)
        self.assertEqual(result["events"][0]["exit_rule"], "fixed_mean_recovery")

    def test_deterministic_order_and_retry(self):
        left, right = self.make(), self.make()
        for minute in (0, 5, 10):
            a = self.tick(left, minute)
            b = self.tick(right, minute, reverse=True)
            self.assertEqual(a, b)
        self.assertEqual(a, self.tick(left, 10))
        with self.assertRaisesRegex(ValueError, "retry changed"):
            self.tick(left, 10, price="99")

    def test_costs_can_block_second_entry_at_combined_cap(self):
        coordinator = self.make(costs="5")
        for minute in (0, 5, 10):
            result = self.tick(coordinator, minute)
        self.assertEqual(sum(event["executed"] for event in result["events"]), 1)
        self.assertEqual(len(coordinator.ledger.positions), 1)

    def test_risk_only_exit(self):
        coordinator = self.make()
        for minute in (0, 5, 10):
            self.tick(coordinator, minute)
        result = self.tick(coordinator, 11, price="90", risk_only=True)
        self.assertEqual(len(result["events"]), 2)
        self.assertTrue(all(event["executed"] for event in result["events"]))
        self.assertTrue(all(event["exit_rule"] == "stop_loss" for event in result["events"]))
        self.assertEqual(coordinator.ledger.positions, {})

    def test_bad_tick_is_atomic(self):
        coordinator = self.make()
        self.tick(coordinator, 0)
        previous = coordinator.last_result
        at = NOW + timedelta(minutes=5)
        with self.assertRaises(ValueError):
            coordinator.step(
                decision_at=at,
                snapshots={("mean_reversion_v2", "SPY"): mean_fixture(at)},
                quotes={"SPY": (D("99"), at - timedelta(seconds=1))},
            )
        self.assertEqual(coordinator.last_result, previous)
        self.assertEqual(coordinator.last_tick, NOW)


if __name__ == "__main__":
    unittest.main()
