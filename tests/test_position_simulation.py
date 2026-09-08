import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from app.capital.position_simulation import PositionSimulation
from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY as POLICY
from app.capital.mean_reversion_math import MeanReversionSnapshot

NOW = datetime(2026, 9, 4, 14, tzinfo=timezone.utc)


def snapshot(at, price="98", age=1):
    return MeanReversionSnapshot(
        symbol="SPY", observation_at=at - timedelta(seconds=age),
        latest_price_usd=D(price), mean_price_usd=D("100"),
        standard_deviation_usd=D("1"), z_score=D("-2"),
        observation_count=48, usable=True, reason=None,
    )


class PositionTests(unittest.TestCase):
    def make(self):
        return PositionSimulation(
            symbol="SPY", policy=POLICY, fee_bps=5, slippage_bps=5
        )

    def enter(self, simulation):
        for minute in (0, 5, 10):
            at = NOW + timedelta(minutes=minute)
            simulation.step(snapshot(at), decision_at=at)
        self.assertEqual(len(simulation.ledger.fills), 1)

    def test_entry_no_duplicate_and_recovery(self):
        simulation = self.make()
        self.enter(simulation)
        at = NOW + timedelta(minutes=15)
        simulation.step(snapshot(at), decision_at=at)
        self.assertEqual(len(simulation.ledger.fills), 1)
        at += timedelta(minutes=5)
        event = simulation.step(snapshot(at, "100"), decision_at=at)
        self.assertTrue(event["executed"])
        self.assertEqual(event["exit_rule"], "fixed_mean_recovery")
        self.assertEqual(simulation.ledger.quantity, 0)
        simulation.ledger.mark(D("100"))

    def test_stale_entry_rejected(self):
        simulation = self.make()
        for minute in (0, 5, 10):
            at = NOW + timedelta(minutes=minute)
            event = simulation.step(snapshot(at, age=121), decision_at=at)
        self.assertFalse(event["executed"])
        self.assertIn("Reference price is stale.", event["reasons"])
        self.assertEqual(simulation.ledger.cash, POLICY.starting_capital_usd)

    def test_timeout_only_on_regular_cycle(self):
        simulation = self.make()
        self.enter(simulation)
        at = simulation.opened_at + timedelta(hours=24)
        simulation.step(snapshot(at), decision_at=at, risk_only=True)
        self.assertGreater(simulation.ledger.quantity, 0)
        at += timedelta(seconds=1)
        event = simulation.step(snapshot(at), decision_at=at)
        self.assertEqual(event["exit_rule"], "recovery_timeout")
        self.assertTrue(event["executed"])

    def test_risk_exit_and_clock_order(self):
        simulation = self.make()
        self.enter(simulation)
        at = NOW + timedelta(minutes=11)
        event = simulation.step(
            snapshot(at, "90"), decision_at=at, risk_only=True
        )
        self.assertTrue(event["executed"])
        self.assertEqual(event["exit_rule"], "stop_loss")
        with self.assertRaises(ValueError):
            simulation.step(snapshot(at), decision_at=at)


if __name__ == "__main__":
    unittest.main()
