from datetime import timedelta
from decimal import Decimal as D
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from app.capital import shadow_checkpoint as checkpoint
from app.capital.shadow_coordinator import ShadowCoordinator
from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY as POLICY

fixtures = runpy.run_path("tests/test_shadow_coordinator.py")
NOW = fixtures["NOW"]
CAPS = {"mean_reversion_v2": "15", "volatility_breakout_v1": "15"}


def tick(minute, price="98"):
    at = NOW + timedelta(minutes=minute)
    return {
        "decision_at": at,
        "snapshots": {
            ("mean_reversion_v2", "SPY"): fixtures["mean_fixture"](at, price),
            ("volatility_breakout_v1", "SPY"): fixtures["breakout_fixture"](
                at - timedelta(seconds=1), price
            ),
        },
        "quotes": {"SPY": (D(price), at - timedelta(seconds=1))},
        "risk_only": False,
    }


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        checkpoint.create_checkpoint(
            self.directory, policy=POLICY, strategy_caps=CAPS,
            fee_bps="0", slippage_bps="0",
        )

    def test_restarts_preserve_confirmation_positions_and_exits(self):
        direct = ShadowCoordinator(
            policy=POLICY, strategy_caps=CAPS, fee_bps="0", slippage_bps="0"
        )
        for minute, price in ((0, "98"), (5, "98"), (10, "98"), (15, "100")):
            inputs = tick(minute, price)
            expected = checkpoint.normalized(direct.step(**inputs))
            self.assertEqual(checkpoint.process_tick(self.directory, **inputs), expected)
        verification = checkpoint.verify_checkpoint(self.directory)
        self.assertEqual(verification["ticks"], 4)
        self.assertEqual(verification["last_result"], expected)

    def test_retry_does_not_append_or_reexecute(self):
        inputs = tick(0)
        first = checkpoint.process_tick(self.directory, **inputs)
        before = (self.directory / "shadow.json").read_bytes()
        self.assertEqual(checkpoint.process_tick(self.directory, **inputs), first)
        self.assertEqual((self.directory / "shadow.json").read_bytes(), before)
        with self.assertRaises(ValueError):
            checkpoint.process_tick(self.directory, **tick(0, "99"))
        self.assertEqual((self.directory / "shadow.json").read_bytes(), before)

    def test_failed_write_can_retry_from_original_checkpoint(self):
        inputs = tick(0)
        before = (self.directory / "shadow.json").read_bytes()
        with patch.object(checkpoint.os, "replace", side_effect=OSError("Synthetic crash")):
            with self.assertRaises(OSError):
                checkpoint.process_tick(self.directory, **inputs)
        self.assertEqual((self.directory / "shadow.json").read_bytes(), before)
        checkpoint.process_tick(self.directory, **inputs)
        self.assertEqual(checkpoint.verify_checkpoint(self.directory)["ticks"], 1)

    def test_tampered_result_rejected_even_with_recomputed_digest(self):
        checkpoint.process_tick(self.directory, **tick(0))
        path = self.directory / "shadow.json"
        envelope = json.loads(path.read_text())
        envelope["state"]["ticks"][0]["result"]["account"]["cash_balance_usd"] = "999999"
        envelope["sha256"] = checkpoint.digest(envelope["state"])
        path.write_text(json.dumps(envelope))
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "replay mismatch"):
            checkpoint.process_tick(self.directory, **tick(5))
        self.assertEqual(path.read_bytes(), before)

    def test_missing_corrupt_and_changed_configuration_rejected(self):
        with self.assertRaises(FileExistsError):
            checkpoint.create_checkpoint(
                self.directory, policy=POLICY, strategy_caps=CAPS,
                fee_bps="0", slippage_bps="0",
            )
        with patch.object(checkpoint, "fingerprint", return_value={"changed": True}):
            with self.assertRaisesRegex(ValueError, "configuration changed"):
                checkpoint.verify_checkpoint(self.directory)
        path = self.directory / "shadow.json"
        path.write_text("{broken")
        with self.assertRaises(ValueError):
            checkpoint.process_tick(self.directory, **tick(0))
        self.assertEqual(path.read_text(), "{broken")


if __name__ == "__main__":
    unittest.main()
