from decimal import Decimal as D
from pathlib import Path
import runpy
import tempfile
import unittest

from app.capital.shadow_risk import downward_volatility_scale
from app.capital.shadow_coordinator import ShadowCoordinator
from app.capital import shadow_checkpoint as checkpoint

fixture = runpy.run_path("tests/test_shadow_checkpoint.py")


class ShadowRiskTests(unittest.TestCase):
    def make(self):
        return ShadowCoordinator(
            policy=fixture["POLICY"], strategy_caps=fixture["CAPS"],
            fee_bps="0", slippage_bps="0",
        )

    def test_reduce_only_blocks_entries(self):
        coordinator = self.make()
        for minute in (0, 5, 10):
            coordinator.step(**fixture["tick"](minute), risk_mode="reduce_only")
        self.assertEqual(coordinator.ledger.positions, {})

    def test_reduce_only_allows_risk_exits(self):
        coordinator = self.make()
        for minute in (0, 5, 10):
            coordinator.step(**fixture["tick"](minute))
        tick = fixture["tick"](11, "90")
        tick["risk_only"] = True
        result = coordinator.step(**tick, risk_mode="reduce_only")
        self.assertEqual(len(result["events"]), 2)
        self.assertTrue(all(event["executed"] for event in result["events"]))
        self.assertEqual(coordinator.ledger.positions, {})

    def test_halted_and_scaled_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint.create_checkpoint(
                Path(tmp), policy=fixture["POLICY"], strategy_caps=fixture["CAPS"],
                fee_bps="0", slippage_bps="0",
            )
            result = checkpoint.process_tick(
                tmp, **fixture["tick"](0), risk_mode="halted"
            )
            self.assertEqual(result["events"], [])
            for minute in (5, 10, 15):
                result = checkpoint.process_tick(
                    tmp, **fixture["tick"](minute),
                    size_scales={name: "0.5" for name in fixture["CAPS"]},
                )
            value = D(result["account"]["market_value_usd"])
            self.assertGreater(value, 99)
            self.assertLessEqual(value, 100)
            self.assertEqual(checkpoint.verify_checkpoint(tmp)["last_result"], result)

    def test_no_upward_scaling_and_insufficient_history(self):
        insufficient = downward_volatility_scale(
            [100, 101], target_volatility_percent="0.1"
        )
        self.assertEqual(insufficient["scale"], 0)
        constant = downward_volatility_scale(
            [100] * 22, target_volatility_percent="0.1"
        )
        self.assertEqual(constant["scale"], 1)
        volatile = downward_volatility_scale(
            [100, 110] * 12, target_volatility_percent="0.1"
        )
        self.assertGreater(volatile["scale"], 0)
        self.assertLess(volatile["scale"], 1)
        with self.assertRaises(ValueError):
            self.make().step(
                **fixture["tick"](0), size_scales={"mean_reversion_v2": "1.1"}
            )


if __name__ == "__main__":
    unittest.main()
