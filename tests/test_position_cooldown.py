"""Loss cooldown tests using the real simulation and ledger."""

import unittest
from dataclasses import replace
from datetime import timedelta

from app.capital.position_simulation import PositionSimulation
from test_position_simulation import NOW, POLICY, snapshot


class PositionCooldownTests(unittest.TestCase):
    def make(self, *, enabled=True, symbol="SPY", fee_bps=5):
        return PositionSimulation(
            symbol=symbol,
            policy=POLICY,
            fee_bps=fee_bps,
            slippage_bps=5,
            loss_reentry_cooldown=enabled,
        )

    def step(self, simulation, at, price="98", **kwargs):
        value = replace(snapshot(at, price), symbol=simulation.symbol)
        return simulation.step(value, decision_at=at, **kwargs)

    def enter(self, simulation):
        for minute in (0, 5, 10):
            self.step(simulation, NOW + timedelta(minutes=minute))
        self.assertEqual(len(simulation.ledger.fills), 1)
        self.assertGreater(simulation.ledger.quantity, 0)

    def lose(self, simulation):
        self.enter(simulation)
        closed_at = NOW + timedelta(minutes=11)
        event = self.step(
            simulation, closed_at, "90", risk_only=True
        )
        self.assertTrue(event["executed"])
        self.assertEqual(event["exit_rule"], "stop_loss")
        self.assertLess(event["fill"]["realized_pnl"], 0)
        return closed_at

    def test_blocks_until_exact_sixty_minute_boundary(self):
        simulation = self.make()
        closed_at = self.lose(simulation)
        boundary = closed_at + timedelta(seconds=3600)
        self.assertEqual(simulation.cooldown_until, boundary)

        for seconds in (2, 3, 3599):
            event = self.step(
                simulation, closed_at + timedelta(seconds=seconds)
            )
            self.assertFalse(event["executed"])
            self.assertEqual(simulation.ledger.quantity, 0)

        self.assertIn("cooldown", event["reasons"][0])
        self.assertEqual(event["cooldown_until"], boundary.isoformat())

        event = self.step(simulation, boundary)
        self.assertTrue(event["executed"])
        self.assertEqual(event["action"], "buy")
        self.assertEqual(len(simulation.ledger.fills), 3)

    def test_default_and_explicit_disabled_behaviors_match(self):
        baseline = PositionSimulation(
            symbol="SPY", policy=POLICY, fee_bps=5, slippage_bps=5
        )
        explicit = self.make(enabled=False)

        for minute, price in (
            (0, "98"), (5, "98"), (10, "98"), (11, "90"),
            (12, "98"), (13, "98"), (14, "98"),
        ):
            at = NOW + timedelta(minutes=minute)
            self.assertEqual(
                self.step(baseline, at, price),
                self.step(explicit, at, price),
            )

        self.assertEqual(baseline.ledger.fills, explicit.ledger.fills)
        self.assertEqual(len(baseline.ledger.fills), 3)
        self.assertIsNone(baseline.cooldown_until)

    def test_profitable_close_does_not_start_cooldown(self):
        simulation = self.make()
        self.enter(simulation)
        at = NOW + timedelta(minutes=11)
        event = self.step(simulation, at, "100")
        self.assertTrue(event["executed"])
        self.assertGreater(event["fill"]["realized_pnl"], 0)
        self.assertIsNone(simulation.cooldown_until)

        for seconds in (2, 3, 4):
            event = self.step(
                simulation, at + timedelta(seconds=seconds)
            )
        self.assertTrue(event["executed"])

    def test_costs_can_turn_flat_reference_prices_into_loss(self):
        simulation = self.make()
        self.enter(simulation)
        at = simulation.opened_at + timedelta(hours=24)
        event = self.step(simulation, at, "98")

        self.assertTrue(event["executed"])
        self.assertEqual(event["exit_rule"], "recovery_timeout")
        self.assertEqual(
            simulation.ledger.fills[0]["reference_price"],
            event["fill"]["reference_price"],
        )
        self.assertLess(event["fill"]["realized_pnl"], 0)
        self.assertEqual(
            simulation.cooldown_until, at + timedelta(seconds=3600)
        )

    def test_accounts_and_assets_have_independent_cooldowns(self):
        losing = self.make()
        same_asset = self.make()
        other_asset = self.make(symbol="BTC")
        closed_at = self.lose(losing)

        for simulation in (same_asset, other_asset):
            for seconds in (1, 2, 3):
                event = self.step(
                    simulation, closed_at + timedelta(seconds=seconds)
                )
            self.assertTrue(event["executed"])
            self.assertIsNone(simulation.cooldown_until)

        self.assertEqual(losing.ledger.quantity, 0)

    def test_protective_exit_remains_available_after_reentry(self):
        simulation = self.make()
        closed_at = self.lose(simulation)
        boundary = closed_at + timedelta(seconds=3600)

        for seconds in (1, 2, 3):
            self.step(
                simulation, closed_at + timedelta(seconds=seconds)
            )
        event = self.step(simulation, boundary)
        self.assertTrue(event["executed"])

        exit_at = boundary + timedelta(seconds=1)
        event = self.step(
            simulation, exit_at, "90", risk_only=True
        )
        self.assertTrue(event["executed"])
        self.assertEqual(event["exit_rule"], "stop_loss")
        self.assertEqual(
            simulation.cooldown_until,
            exit_at + timedelta(seconds=3600),
        )

    def test_rejected_future_observation_cannot_start_cooldown(self):
        simulation = self.make()
        self.enter(simulation)
        at = NOW + timedelta(minutes=11)
        future = replace(
            snapshot(at, "90"),
            observation_at=at + timedelta(seconds=1),
        )
        before = list(simulation.ledger.fills)

        with self.assertRaisesRegex(ValueError, "precede decision"):
            simulation.step(future, decision_at=at)

        self.assertEqual(simulation.ledger.fills, before)
        self.assertIsNone(simulation.cooldown_until)
        self.assertGreater(simulation.ledger.quantity, 0)

    def test_nonboolean_setting_is_rejected(self):
        for value in (1, 0, "true", None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "boolean"):
                    self.make(enabled=value)


if __name__ == "__main__":
    unittest.main()
