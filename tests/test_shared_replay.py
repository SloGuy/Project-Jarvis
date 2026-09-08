import hashlib
import json
import runpy
import unittest
from unittest.mock import patch

from app.capital import run_shared_shadow as shared
from app.capital import validation_registry as registry

integration = runpy.run_path("tests/test_validation_integration.py")
inputs = runpy.run_path("tests/test_shadow_inputs.py")


class SharedReplayTests(unittest.TestCase):
    def setUp(self):
        integration["ValidationIntegrationTests"].setUp(self)
        for target, name, value in (
            (shared, "OUTPUT_DIRECTORY", self.directory / "shared"),
            (shared, "SessionLocal", integration["evaluation"].SessionLocal),
        ):
            handle = patch.object(target, name, value)
            handle.start()
            self.addCleanup(handle.stop)

    def run_replay(self):
        return shared.run_shared(
            allocation=inputs["allocation"](),
            assets=[(1, "Finnhub")],
            start=integration["START"], end=integration["END"],
            fee_bps="5", slippage_bps="5",
        )

    def test_full_shared_historical_replay(self):
        # This synthetic fixture was reserved by the validation test setup.
        # Remove only its temporary reservation for the development test.
        with registry.locked_state(write=True) as state:
            state["plans"].clear()
        directory = self.run_replay()
        summary = json.loads((directory / "summary.json").read_text())
        result = json.loads((directory / "result.json").read_text())
        self.assertEqual(summary["ticks"], 16)
        self.assertEqual(summary["verification_status"], "matched")
        self.assertEqual(
            set(summary["strategies"]),
            {"mean_reversion_v2", "volatility_breakout_v1"},
        )
        self.assertGreaterEqual(summary["executed_orders"], 2)
        for name, expected in result["artifacts_sha256"].items():
            self.assertEqual(
                hashlib.sha256((directory / name).read_bytes()).hexdigest(),
                expected,
            )
        self.assertFalse(result["promotion_authorized"])
        self.assertEqual(
            integration["research_store"].RESEARCH_STATE_FILE.read_bytes(),
            self.research_before,
        )

    def test_reserved_period_blocks_shared_replay(self):
        with self.assertRaisesRegex(ValueError, "Reserved"):
            self.run_replay()
        directory = next(shared.OUTPUT_DIRECTORY.iterdir())
        self.assertTrue((directory / "failure.json").exists())
        self.assertFalse((directory / "result.json").exists())



    def test_scaled_replay_reduces_entry_quantity(self):
        from decimal import Decimal
        with registry.locked_state(write=True) as state:
            state["plans"].clear()
        baseline = self.run_replay()
        scaled = shared.run_shared(
            allocation=inputs["allocation"](), assets=[(1, "Finnhub")],
            start=integration["START"], end=integration["END"],
            fee_bps="5", slippage_bps="5",
            target_volatility_percent="0.001",
        )

        def bought(directory):
            state = json.loads(
                (directory / "checkpoint/shadow.json").read_text()
            )["state"]
            return sum(
                Decimal(event["fill"]["quantity"])
                for tick in state["ticks"]
                for event in tick["result"]["events"]
                if event["executed"] and event["side"] == "buy"
            )

        self.assertGreater(bought(scaled), 0)
        self.assertLess(bought(scaled), bought(baseline))
        summary = json.loads((scaled / "summary.json").read_text())
        for limits in summary["sizing_scale_range"].values():
            self.assertGreaterEqual(Decimal(limits["minimum"]), 0)
            self.assertLessEqual(Decimal(limits["maximum"]), 1)

    def test_halted_historical_replay_places_no_orders(self):
        with registry.locked_state(write=True) as state:
            state["plans"].clear()
        directory = shared.run_shared(
            allocation=inputs["allocation"](), assets=[(1, "Finnhub")],
            start=integration["START"], end=integration["END"],
            fee_bps="5", slippage_bps="5", risk_mode="halted",
        )
        summary = json.loads((directory / "summary.json").read_text())
        self.assertEqual(summary["executed_orders"], 0)
        self.assertEqual(summary["rejected_orders"], 0)


if __name__ == "__main__":
    unittest.main()
