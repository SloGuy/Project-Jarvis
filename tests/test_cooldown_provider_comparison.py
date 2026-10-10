"""Provider comparison orchestration tests with isolated source fixtures."""

import unittest
from contextlib import ExitStack, nullcontext
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from unittest.mock import patch

from app.capital import cooldown_provider_comparison as connector
from app.capital.cooldown_manifest import capture_cooldown_manifest, _plain
from test_position_simulation import NOW, POLICY, snapshot


class FixtureHistory:
    """Supply deterministic snapshots; receipt verification is mocked here."""

    def __init__(self, **kwargs):
        self.arguments = kwargs

    def window(self, *, decision_at):
        at = datetime.fromisoformat(decision_at)
        minute = int((at - NOW).total_seconds() / 60)
        value = snapshot(at, "90" if minute == 11 else "98")
        record_id = f"fixture-record-{minute}"
        return {
            "snapshot": value,
            "observation_ids": [record_id],
            "provider_input": {
                "observations": [{
                    "record_id": record_id,
                    "price_usd": str(value.latest_price_usd),
                    "observed_at": value.observation_at.isoformat(),
                }],
            },
        }


class CooldownProviderComparisonTests(unittest.TestCase):
    def setUp(self):
        self.manifest = capture_cooldown_manifest(
            policy=POLICY, fee_bps=5, slippage_bps=5
        )
        self.plan = {
            "schema_version": 2,
            "symbol": "SPY",
            "policy": _plain(asdict(POLICY)),
            "execution_manifest": self.manifest["replay_manifest"],
            "fee_bps": "5",
            "slippage_bps": "5",
            "start": NOW.isoformat(),
            "end_exclusive": (NOW + timedelta(minutes=30)).isoformat(),
        }
        self.row = {
            "plan_id": "fixture-plan",
            "status": "completed",
            "registered_sha256": "a" * 64,
            "envelope": {"plan": deepcopy(self.plan)},
            "provider_collection": {
                "status": "sealed",
                "bound_at": (NOW - timedelta(hours=1)).isoformat(),
            },
        }
        self.materialized = {
            "collection": deepcopy(self.row["provider_collection"]),
            "receipts": [{"fixture": True}],
        }

        stack = ExitStack()
        self.addCleanup(stack.close)

        def mock(name, **kwargs):
            return stack.enter_context(
                patch.object(connector, name, **kwargs)
            )

        self.authorize = mock("authorize_read")
        self.get_plan = stack.enter_context(
            patch.object(
                connector.registry,
                "get_plan",
                side_effect=lambda _: deepcopy(self.row),
            )
        )
        self.require_plan = mock(
            "require_provider_plan",
            side_effect=lambda _: deepcopy(self.plan),
        )
        self.materialize = mock(
            "materialize_provider_collection",
            side_effect=lambda _: deepcopy(self.materialized),
        )
        self.packet = mock(
            "open_provider_packet",
            side_effect=lambda *args, **kwargs: nullcontext(
                FixtureHistory()
            ),
        )

    def run_comparison(self, **changes):
        arguments = {
            "plan_id": "fixture-plan",
            "policy": POLICY,
            "saved_manifest": self.manifest,
        }
        arguments.update(changes)
        return connector.run_provider_comparison(**arguments)

    def test_matching_source_runs_independent_accounts(self):
        before = deepcopy(self.row)
        result = self.run_comparison()

        self.assertEqual(result["decision_count"], 30)
        self.assertEqual(len(result["windows"]), 30)
        self.assertEqual(len(result["baseline"]["fills"]), 3)
        self.assertEqual(len(result["intervention"]["fills"]), 2)
        self.assertEqual(self.row, before)
        self.assertEqual(self.materialize.call_count, 2)
        self.assertEqual(self.authorize.call_count, 3)
        self.assertEqual(
            result["provider_evidence"]["collection"],
            self.row["provider_collection"],
        )

    def test_unfinished_original_plan_is_rejected_before_reads(self):
        self.row["status"] = "running"
        with self.assertRaisesRegex(ValueError, "completed first"):
            self.run_comparison()
        self.materialize.assert_not_called()
        self.packet.assert_not_called()

    def test_policy_mismatch_is_rejected(self):
        changed = replace(
            POLICY, max_price_age_seconds=POLICY.max_price_age_seconds + 1
        )
        with self.assertRaisesRegex(ValueError, "policy differs"):
            self.run_comparison(policy=changed)
        self.materialize.assert_not_called()

    def test_changed_manifest_is_rejected_before_materialization(self):
        changed = deepcopy(self.manifest)
        changed["comparison_rules"]["cooldown_seconds"] = 1
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            self.run_comparison(saved_manifest=changed)
        self.materialize.assert_not_called()

    def test_collection_failures_propagate_without_packet_fallback(self):
        for message in (
            "Provider collection is not sealed.",
            "Provider checkpoint differs from registry; recovery required.",
        ):
            with self.subTest(message=message):
                self.materialize.side_effect = ValueError(message)
                with self.assertRaisesRegex(ValueError, message):
                    self.run_comparison()
        self.packet.assert_not_called()

    def test_embedded_packet_failure_is_propagated(self):
        self.packet.side_effect = ValueError("Receipt chain changed.")
        with self.assertRaisesRegex(ValueError, "Receipt chain changed"):
            self.run_comparison()
        self.assertEqual(self.materialize.call_count, 1)

    def test_registry_change_during_comparison_is_rejected(self):
        changed = deepcopy(self.row)
        changed["provider_collection"]["bound_at"] = NOW.isoformat()
        self.get_plan.side_effect = [
            deepcopy(self.row), changed
        ]
        with self.assertRaisesRegex(ValueError, "state changed"):
            self.run_comparison()

    def test_initial_authority_denial_prevents_registry_read(self):
        self.authorize.side_effect = PermissionError("Denied.")
        with self.assertRaises(PermissionError):
            self.run_comparison()
        self.get_plan.assert_not_called()
        self.materialize.assert_not_called()

    def test_final_authority_denial_prevents_return(self):
        self.authorize.side_effect = [
            None, None, PermissionError("Denied.")
        ]
        with self.assertRaises(PermissionError):
            self.run_comparison()

    def test_result_does_not_claim_registered_comparison_or_authority(self):
        result = self.run_comparison()
        for field in (
            "comparison_preregistered",
            "comparison_packet_verified",
            "validation_ready",
            "registry_writes",
            "database_writes",
            "promotion_authorized",
            "strategy_change_authorized",
            "live_capital_authorized",
        ):
            self.assertIs(result[field], False)


if __name__ == "__main__":
    unittest.main()
