from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from app.capital.validation_plan import (
    BENCHMARK, check_binding, plan_digest, seal_plan, verify_plan,
)


def draft():
    return {
        "schema_version": 1,
        "designation": "prospective_validation",
        "created_by": "synthetic_test",
        "research": {
            "research_id": "synthetic",
            "hypothesis_version": 1,
            "strategy_name": "synthetic",
            "hypothesis": "Synthetic hypothesis",
            "asset_universe": ["SPY"],
            "success_criteria": ["Synthetic criterion"],
        },
        "strategy_version": "test",
        "asset_id": 1, "symbol": "SPY", "provider": "Finnhub",
        "start": "2030-01-02T14:00:00+00:00",
        "end_exclusive": "2030-01-02T20:00:00+00:00",
        "fee_bps": "5", "slippage_bps": "5",
        "benchmark": BENCHMARK,
        "criteria": {
            "minimum_completed_trades": 10,
            "maximum_stale_tick_percent": "5",
            "maximum_unusable_regular_tick_percent": "10",
            "minimum_return_percent": "0",
            "minimum_excess_return_percent": "0",
            "maximum_drawdown_percent": "5",
        },
        "policy": {"name": "synthetic"},
        "execution_manifest": {"synthetic": True},
    }


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


class PlanTests(unittest.TestCase):
    def test_roundtrip_and_independent_copy(self):
        original = draft()
        packet = seal_plan(original, now=NOW)
        plan = verify_plan(packet, expected_sha256=packet["sha256"])
        original["criteria"]["minimum_completed_trades"] = 999
        self.assertEqual(plan["criteria"]["minimum_completed_trades"], 10)

    def test_past_period_and_naive_time_rejected(self):
        for start in (
            "2029-01-01T14:00:00+00:00",
            "2030-01-02T14:00:00",
        ):
            value = draft()
            value["start"] = start
            with self.assertRaises(ValueError):
                seal_plan(value, now=NOW)

    def test_tamper_even_with_recomputed_embedded_hash(self):
        packet = seal_plan(draft(), now=NOW)
        retained = packet["sha256"]
        packet["plan"]["fee_bps"] = "0"
        packet["sha256"] = plan_digest(packet["plan"])
        with self.assertRaisesRegex(ValueError, "changed"):
            verify_plan(packet, expected_sha256=retained)

    def test_invalid_criteria_rejected(self):
        for key, value in (
            ("minimum_completed_trades", True),
            ("minimum_completed_trades", 0),
            ("maximum_stale_tick_percent", "101"),
            ("minimum_return_percent", "NaN"),
        ):
            with self.subTest(key=key, value=value):
                item = draft()
                item["criteria"][key] = value
                with self.assertRaises(ValueError):
                    seal_plan(item, now=NOW)
        item = draft()
        del item["criteria"]["minimum_return_percent"]
        with self.assertRaises(ValueError):
            seal_plan(item, now=NOW)

    def test_binding_changes_rejected(self):
        plan = seal_plan(draft(), now=NOW)["plan"]
        research = deepcopy(plan["research"])
        candidate = SimpleNamespace(to_dict=lambda: research)
        check_binding(
            plan, candidate, "test", plan["policy"], plan["execution_manifest"]
        )
        for key, value in (
            ("hypothesis_version", 2),
            ("hypothesis", "Changed"),
            ("success_criteria", ["Changed"]),
        ):
            old = research[key]
            research[key] = value
            with self.assertRaises(ValueError):
                check_binding(
                    plan, candidate, "test",
                    plan["policy"], plan["execution_manifest"],
                )
            research[key] = old
        for version, policy, manifest in (
            ("changed", plan["policy"], plan["execution_manifest"]),
            ("test", {"changed": True}, plan["execution_manifest"]),
            ("test", plan["policy"], {"changed": True}),
        ):
            with self.assertRaises(ValueError):
                check_binding(plan, candidate, version, policy, manifest)


if __name__ == "__main__":
    unittest.main()
