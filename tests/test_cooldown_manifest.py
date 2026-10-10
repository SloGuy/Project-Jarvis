"""Verify cooldown comparison configuration fingerprints."""

import unittest
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, localcontext
from unittest.mock import patch

from app.capital import cooldown_manifest as manifest
from test_position_simulation import POLICY


class CooldownManifestTests(unittest.TestCase):
    def capture(self, **changes):
        arguments = {
            "policy": POLICY,
            "fee_bps": 5,
            "slippage_bps": 5,
        }
        arguments.update(changes)
        return manifest.capture_cooldown_manifest(**arguments)

    def verify(self, saved, **changes):
        arguments = {
            "policy": POLICY,
            "fee_bps": 5,
            "slippage_bps": 5,
        }
        arguments.update(changes)
        return manifest.verify_cooldown_manifest(saved, **arguments)

    def test_real_manifest_can_be_captured_and_verified(self):
        saved = self.capture()
        self.assertEqual(self.verify(saved), saved)
        self.assertEqual(len(saved["manifest_sha256"]), 64)
        self.assertIn(
            "app/capital/position_simulation.py",
            saved["replay_manifest"]["source_sha256"],
        )
        self.assertEqual(
            set(saved["comparison_source_sha256"]),
            set(manifest.COMPARISON_SOURCES),
        )

    def test_cost_changes_are_rejected(self):
        saved = self.capture()
        for changes in (
            {"fee_bps": 6},
            {"slippage_bps": 6},
        ):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, "changed"):
                    self.verify(saved, **changes)

    def test_policy_change_is_rejected(self):
        saved = self.capture()
        changed = replace(
            POLICY,
            max_price_age_seconds=POLICY.max_price_age_seconds + 1,
        )
        with self.assertRaisesRegex(ValueError, "changed"):
            self.verify(saved, policy=changed)

    def test_replay_source_change_is_rejected(self):
        saved = self.capture()
        changed = deepcopy(saved["replay_manifest"])
        changed["source_sha256"][
            "app/capital/position_simulation.py"
        ] = "0" * 64
        with patch.object(
            manifest, "capture_replay_manifest", return_value=changed
        ):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.verify(saved)

    def test_provider_source_change_is_rejected(self):
        saved = self.capture()
        changed = deepcopy(saved["provider_source_sha256"])
        name = next(iter(changed))
        changed[name] = "0" * 64
        with patch.object(
            manifest, "capture_input_sources", return_value=changed
        ):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.verify(saved)

    def test_decimal_context_change_is_rejected(self):
        saved = self.capture()
        with localcontext() as context:
            context.prec += 1
            with self.assertRaisesRegex(ValueError, "changed"):
                self.verify(saved)

    def test_rehashed_changed_cooldown_rule_is_rejected(self):
        saved = self.capture()
        saved["comparison_rules"]["cooldown_seconds"] = 1800
        body = {
            key: value
            for key, value in saved.items()
            if key != "manifest_sha256"
        }
        saved["manifest_sha256"] = manifest._digest(body)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.verify(saved)

    def test_changed_comparison_fingerprint_is_rejected(self):
        saved = self.capture()
        name = manifest.COMPARISON_SOURCES[0]
        saved["comparison_source_sha256"][name] = "0" * 64
        with self.assertRaisesRegex(ValueError, "changed"):
            self.verify(saved)

    def test_verified_result_is_an_independent_copy(self):
        saved = self.capture()
        verified = self.verify(saved)
        verified["comparison_rules"]["cooldown_seconds"] = 1
        self.assertEqual(
            saved["comparison_rules"]["cooldown_seconds"], 3600
        )
        self.assertEqual(self.verify(saved), saved)

    def test_invalid_costs_are_rejected(self):
        for value in (True, -1, 10000, Decimal("NaN")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.capture(fee_bps=value)

    def test_manifest_grants_no_authority(self):
        saved = self.capture()
        for field in (
            "registration_verified",
            "input_availability_verified",
            "promotion_authorized",
            "live_capital_authorized",
        ):
            self.assertIs(saved[field], False)


if __name__ == "__main__":
    unittest.main()
