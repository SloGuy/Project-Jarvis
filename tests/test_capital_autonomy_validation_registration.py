"""Validation registration tests using an isolated registry."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.capital import validation_registry as registry
from app.capital.validation_plan import BENCHMARK
from app.capital.autonomy_validation_registration import register_validation_once


NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


class ValidationRegistrationRetryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)

        mocked = patch.object(registry, "DIRECTORY", Path(temporary.name))
        mocked.start()
        self.addCleanup(mocked.stop)

        mocked = patch.object(registry, "now_utc", return_value=NOW)
        self.clock = mocked.start()
        self.addCleanup(mocked.stop)

        self.binding = Mock()
        self.draft = {
            "schema_version": 1,
            "designation": "prospective_validation",
            "created_by": "capital.validation",
            "research": {
                "research_id": "research_test",
                "hypothesis_version": 1,
                "strategy_name": "mean_reversion_v2",
                "hypothesis": "Test a predeclared hypothesis.",
                "asset_universe": ["BTC"],
                "success_criteria": ["Meet registered numeric criteria"],
            },
            "strategy_version": "2.0",
            "asset_id": 2,
            "symbol": "BTC",
            "provider": "CoinGecko",
            "start": (NOW + timedelta(hours=1)).isoformat(),
            "end_exclusive": (NOW + timedelta(days=6, hours=1)).isoformat(),
            "fee_bps": "5",
            "slippage_bps": "5",
            "benchmark": BENCHMARK,
            "criteria": {
                "minimum_completed_trades": 30,
                "maximum_stale_tick_percent": "5",
                "maximum_unusable_regular_tick_percent": "10",
                "minimum_return_percent": "0",
                "minimum_excess_return_percent": "0",
                "maximum_drawdown_percent": "5",
            },
            # Binding is mocked; these are not production configurations.
            "policy": {"test_fixture": True},
            "execution_manifest": {"test_fixture": True},
        }

    def register(self, draft=None, key="task:validation-test"):
        value = self.draft if draft is None else draft
        if key is None:
            return registry._register_plan(
                value, validate_binding=self.binding
            )
        with patch.object(
            registry,
            "register_plan",
            side_effect=lambda plan: registry._register_plan(
                plan, validate_binding=self.binding
            ),
        ):
            return register_validation_once(value, request_key=key)

    def test_identical_retry_returns_same_plan(self):
        first = self.register()
        second = self.register()
        self.assertEqual(first, second)
        self.binding.assert_called_once()
        with registry.locked_state() as state:
            self.assertEqual(len(state["plans"]), 1)

    def test_retry_after_start_does_not_reregister(self):
        first = self.register()
        self.clock.return_value = NOW + timedelta(days=2)
        self.assertEqual(self.register(), first)

    def test_retry_preserves_completed_status(self):
        first = self.register()
        with registry.locked_state(write=True) as state:
            row = state["plans"][first["plan_id"]]
            row["status"] = "completed"
            row["history"].append({
                "status": "completed",
                "at": (NOW + timedelta(days=7)).isoformat(),
                "detail": "Test completion",
            })
        result = self.register()
        self.assertEqual(result["plan_id"], first["plan_id"])
        self.assertEqual(result["status"], "completed")

    def test_changed_inputs_reject_same_key(self):
        self.register()
        changed = deepcopy(self.draft)
        changed["fee_bps"] = "6"
        with self.assertRaises(ValueError):
            self.register(changed)

    def test_another_key_cannot_bypass_overlap(self):
        self.register()
        with self.assertRaises(ValueError):
            self.register(key="another-request")

    def test_legacy_registration_still_rejects_overlap(self):
        self.register(key=None)
        with self.assertRaises(ValueError):
            self.register(key=None)

    def test_binding_failure_saves_no_plan_or_request(self):
        self.binding.side_effect = ValueError("Binding changed")
        with self.assertRaises(ValueError):
            self.register()
        with registry.locked_state() as state:
            self.assertEqual(state["plans"], {})
            self.assertNotIn("registration_requests", state)

    def test_duplicate_request_markers_are_rejected(self):
        first = self.register()
        with registry.locked_state(write=True) as state:
            duplicate = deepcopy(state["plans"][first["plan_id"]])
            duplicate["plan_id"] = "validation_duplicate_fixture"
            state["plans"][duplicate["plan_id"]] = duplicate
        with self.assertRaises(RuntimeError):
            self.register()

    def test_invalid_request_keys_are_rejected(self):
        for key in ("", " ", True, 123, "x" * 201):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.register(key=key)
        self.binding.assert_not_called()

    def test_input_and_return_values_do_not_mutate_saved_request(self):
        original = deepcopy(self.draft)
        result = self.register()
        self.assertEqual(self.draft, original)
        result["envelope"]["plan"]["fee_bps"] = "999"
        saved = self.register()
        self.assertEqual(saved["envelope"]["plan"]["fee_bps"], "5")


if __name__ == "__main__":
    unittest.main()
