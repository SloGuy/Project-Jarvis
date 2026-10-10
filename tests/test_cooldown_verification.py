"""Verify serialized comparisons using real embedded provider receipts."""

import json
import unittest
from copy import deepcopy

import test_cooldown_provider_integration as integration_fixture

from app.capital.cooldown_manifest import _plain
from app.capital.cooldown_verification import verify_cooldown_packet
from app.capital.run_evaluation import POLICY
from app.capital.validation_provider_collection import quote_directory
from app.capital import validation_registry as registry


class CooldownVerificationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = integration_fixture.CooldownProviderIntegrationTests(
            methodName="test_real_collection_supplies_both_accounts_without_database"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare_completed()

        self.row = registry.get_plan(self.fixture.fixture.plan_id)
        self.expected = {
            "policy": POLICY,
            "expected_manifest": deepcopy(self.fixture.manifest),
            "expected_plan_id": self.row["plan_id"],
            "expected_sha256": self.row["registered_sha256"],
            "expected_collection": deepcopy(self.row["provider_collection"]),
        }

        report = self.fixture.compare()
        self.path = (
            self.fixture.fixture.base.directory / "cooldown-report.json"
        )
        self.path.write_text(
            json.dumps(_plain(report), allow_nan=False),
            encoding="utf-8",
        )
        self.report = json.loads(self.path.read_text(encoding="utf-8"))

    def verify(self, report=None, **changes):
        arguments = deepcopy(self.expected)
        arguments.update(changes)
        return verify_cooldown_packet(
            self.report if report is None else report,
            **arguments,
        )

    def test_serialized_packet_reconstructs_and_matches(self):
        result = self.verify()
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["decision_count"], 3)
        self.assertFalse(result["comparison_preregistered"])
        self.assertFalse(result["promotion_authorized"])
        self.assertFalse(result["live_capital_authorized"])
        self.fixture.fixture.database.assert_not_called()

    def test_saved_packet_verifies_without_original_quotes(self):
        for path in quote_directory(self.fixture.fixture.row).glob("*.json"):
            path.unlink()
        self.assertEqual(self.verify()["status"], "matched")
        self.fixture.fixture.database.assert_not_called()

    def test_changed_snapshot_and_schedule_are_rejected(self):
        cases = []
        changed = deepcopy(self.report)
        changed["windows"][0]["snapshot"]["latest_price_usd"] = "1"
        cases.append(changed)

        changed = deepcopy(self.report)
        changed["windows"][0]["risk_only"] = True
        cases.append(changed)

        changed = deepcopy(self.report)
        changed["windows"][0]["observation_ids"] = []
        cases.append(changed)

        changed = deepcopy(self.report)
        changed["windows"] = changed["windows"][:-1]
        cases.append(changed)

        for index, changed in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(ValueError):
                    self.verify(changed)

    def test_changed_fills_events_and_summaries_are_rejected(self):
        for account in ("baseline", "intervention"):
            for field, value in (
                ("fills", [{"side": "buy", "fee": "0"}]),
                ("closed_trade_count", 999),
                ("net_realized_usd", "999"),
                ("cash_usd", "999"),
                ("cooldown_until", "2026-01-01T00:00:00+00:00"),
            ):
                with self.subTest(account=account, field=field):
                    changed = deepcopy(self.report)
                    changed[account][field] = value
                    with self.assertRaisesRegex(ValueError, "report metadata"):
                        self.verify(changed)

            changed = deepcopy(self.report)
            changed[account]["events"][0]["executed"] = True
            with self.assertRaises(ValueError):
                self.verify(changed)

    def test_changed_receipts_are_rejected(self):
        changed = deepcopy(self.report)
        changed["provider_evidence"]["receipts"] = []
        with self.assertRaises(ValueError):
            self.verify(changed)

    def test_changed_retained_collection_is_rejected(self):
        changed = deepcopy(self.expected["expected_collection"])
        changed["store_checkpoint"]["head"] = "0" * 64
        with self.assertRaises(ValueError):
            self.verify(expected_collection=changed)

    def test_changed_manifest_or_source_identity_is_rejected(self):
        changed = deepcopy(self.report)
        changed["comparison_manifest"]["comparison_rules"][
            "cooldown_seconds"
        ] = 1800
        with self.assertRaisesRegex(ValueError, "retained reference"):
            self.verify(changed)

        with self.assertRaisesRegex(ValueError, "registration differs"):
            self.verify(expected_plan_id="another-plan")

        with self.assertRaisesRegex(ValueError, "registration differs"):
            self.verify(expected_sha256="0" * 64)

    def test_authority_flags_cannot_be_changed(self):
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
            with self.subTest(field=field):
                changed = deepcopy(self.report)
                changed[field] = True
                with self.assertRaises(ValueError):
                    self.verify(changed)

    def test_extra_or_missing_report_fields_are_rejected(self):
        changed = deepcopy(self.report)
        changed["unverified_claim"] = "improved"
        with self.assertRaises(ValueError):
            self.verify(changed)

        changed = deepcopy(self.report)
        del changed["limitations"]
        with self.assertRaises(ValueError):
            self.verify(changed)

    def test_verification_preserves_inputs_and_registry(self):
        before_report = deepcopy(self.report)
        before_expected = deepcopy(self.expected)
        before_row = registry.get_plan(self.row["plan_id"])

        self.verify()

        self.assertEqual(self.report, before_report)
        self.assertEqual(self.expected, before_expected)
        self.assertEqual(
            registry.get_plan(self.row["plan_id"]), before_row
        )


if __name__ == "__main__":
    unittest.main()
