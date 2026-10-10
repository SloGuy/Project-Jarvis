"""Cooldown comparison with real temporary registry and receipt storage."""

import unittest
from copy import deepcopy
from unittest.mock import patch

import test_validation_provider_integration as fixture_module

from app.capital import cooldown_provider_comparison as connector
from app.capital import validation_registry as registry
from app.capital.cooldown_manifest import capture_cooldown_manifest
from app.capital.run_evaluation import POLICY
from app.capital.validation_provider_collection import quote_directory


class CooldownProviderIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.ProviderIntegrationTests(
            methodName="test_replay_and_assessment_complete_without_database"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

        pending = patch.object(connector, "authorize_read")
        self.authorize = pending.start()
        self.addCleanup(pending.stop)

        self.manifest = capture_cooldown_manifest(
            policy=POLICY,
            fee_bps=self.fixture.plan["fee_bps"],
            slippage_bps=self.fixture.plan["slippage_bps"],
        )

    def prepare_completed(self):
        self.fixture.prepare_collection()
        directory = self.fixture.evaluate()
        self.fixture.assess(directory)
        registry.finish_plan(
            self.fixture.plan_id,
            self.fixture.row["run_token"],
            succeeded=True,
            detail=str(directory / "assessment.json"),
        )

    def compare(self):
        return connector.run_provider_comparison(
            plan_id=self.fixture.plan_id,
            policy=POLICY,
            saved_manifest=self.manifest,
        )

    def test_real_collection_supplies_both_accounts_without_database(self):
        self.prepare_completed()
        before = registry.get_plan(self.fixture.plan_id)
        result = self.compare()

        self.assertEqual(result["decision_count"], 3)
        self.assertEqual(len(result["windows"]), 3)
        self.assertEqual(result["baseline"], result["intervention"])
        self.assertTrue(result["collection_chain_verified"])
        self.assertTrue(result["retained_checkpoint_verified"])
        self.assertEqual(
            registry.get_plan(self.fixture.plan_id), before
        )
        self.fixture.database.assert_not_called()

    def test_original_quote_files_are_not_required(self):
        self.prepare_completed()
        before = self.compare()
        for path in quote_directory(self.fixture.row).glob("*.json"):
            path.unlink()

        after = self.compare()
        self.assertEqual(before, after)
        self.fixture.database.assert_not_called()

    def test_checkpoint_drift_is_rejected(self):
        self.prepare_completed()
        with registry.locked_state(write=True) as state:
            row = state["plans"][self.fixture.plan_id]
            changed = deepcopy(row["provider_collection"])
            changed["store_checkpoint"]["head"] = "0" * 64
            row["provider_collection"] = changed

        with self.assertRaises(ValueError):
            self.compare()
        self.fixture.database.assert_not_called()

    def test_uncompleted_source_is_rejected(self):
        self.fixture.prepare_collection()
        with self.assertRaisesRegex(ValueError, "completed first"):
            self.compare()
        self.fixture.database.assert_not_called()

    def test_comparison_remains_development_evidence(self):
        self.prepare_completed()
        result = self.compare()

        self.assertEqual(
            result["source_validation_sha256"], self.fixture.sha
        )
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
