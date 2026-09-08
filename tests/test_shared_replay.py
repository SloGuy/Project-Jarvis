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


if __name__ == "__main__":
    unittest.main()
