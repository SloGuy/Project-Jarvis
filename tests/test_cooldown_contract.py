"""Prospective cooldown terms use real plans and completed research fixtures."""

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

import test_trade_research_runner as research_fixtures
import test_validation_provider_integration as provider_fixtures

from app.capital import autonomy_trade_research as queue
from app.capital.cooldown_contract import (
    build_cooldown_contract,
    verify_cooldown_contract,
)
from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY as POLICY
from app.capital.validation_plan import seal_plan


class CooldownContractTests(unittest.TestCase):
    def setUp(self):
        research = research_fixtures.TradeResearchRunnerTests(
            methodName="test_proposal_is_saved_with_inputs_and_hash"
        )
        self.addCleanup(research.doCleanups)
        research.setUp()
        research.run_worker()
        self.request = copy.deepcopy(research.saved())
        self.model = research.model

        provider = provider_fixtures.ProviderIntegrationTests(
            methodName="test_replay_and_assessment_complete_without_database"
        )
        self.addCleanup(provider.doCleanups)
        provider.setUp()

        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.request["completed_at"] = (
            self.now - timedelta(minutes=2)
        ).isoformat()

        draft = copy.deepcopy(provider.plan)
        draft.pop("created_at")
        draft["start"] = (self.now + timedelta(hours=1)).isoformat()
        draft["end_exclusive"] = (
            self.now + timedelta(hours=1, minutes=3)
        ).isoformat()
        envelope = seal_plan(
            draft, now=self.now - timedelta(minutes=1)
        )

        self.row = copy.deepcopy(provider.row)
        self.row.update({
            "status": "registered",
            "envelope": envelope,
            "registered_sha256": envelope["sha256"],
        })
        self.row.pop("run_token", None)
        self.row.pop("provider_collection", None)
        self.row.pop("witness_collection", None)

    def build(self, **overrides):
        arguments = {
            "source_row": self.row,
            "request": self.request,
            "policy": POLICY,
            "now": self.now,
        }
        arguments.update(overrides)
        return build_cooldown_contract(**arguments)

    def verify(self, saved, **overrides):
        arguments = {
            "expected_sha256": saved["sha256"],
            "source_row": self.row,
            "request": self.request,
            "policy": POLICY,
        }
        arguments.update(overrides)
        return verify_cooldown_contract(saved, **arguments)

    def test_real_completed_request_and_plan_produce_contract(self):
        saved = self.build()
        verified = self.verify(saved)
        self.assertEqual(verified, saved["contract"])
        self.assertEqual(
            saved["sha256"], queue._digest(saved["contract"])
        )
        self.assertEqual(
            verified["source_plan_sha256"],
            self.row["registered_sha256"],
        )
        self.assertEqual(
            verified["origin_request_sha256"],
            queue._digest(self.request),
        )
        self.model.assert_called_once()

    def test_source_criteria_are_preserved_and_floors_apply(self):
        body = self.build()["contract"]
        self.assertEqual(
            body["source_criteria"],
            self.row["envelope"]["plan"]["criteria"],
        )
        criteria = body["comparison_criteria"]
        self.assertGreaterEqual(
            criteria["minimum_completed_trades_per_account"], 100
        )
        self.assertGreaterEqual(
            Decimal(criteria["minimum_intervention_profit_factor"]),
            Decimal("1.2"),
        )
        self.assertEqual(body["schedule"]["step_seconds"], 60)
        self.assertEqual(body["schedule"]["regular_every_ticks"], 5)

    def test_exact_thirty_minute_lead_is_allowed(self):
        start = datetime.fromisoformat(
            self.row["envelope"]["plan"]["start"]
        )
        saved = self.build(now=start - timedelta(minutes=30))
        self.verify(saved)

    def test_less_than_thirty_minute_lead_is_rejected(self):
        start = datetime.fromisoformat(
            self.row["envelope"]["plan"]["start"]
        )
        with self.assertRaises(ValueError):
            self.build(
                now=start - timedelta(minutes=30) + timedelta(seconds=1)
            )

    def test_contract_before_plan_creation_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build(now=self.now - timedelta(minutes=2))

    def test_contract_before_research_completion_is_rejected(self):
        changed = copy.deepcopy(self.request)
        changed["completed_at"] = (
            self.now + timedelta(seconds=1)
        ).isoformat()
        with self.assertRaises(ValueError):
            self.build(request=changed)

    def test_noncompleted_request_is_rejected(self):
        for status in ("queued", "running", "failed"):
            with self.subTest(status=status):
                changed = copy.deepcopy(self.request)
                changed["status"] = status
                with self.assertRaises(ValueError):
                    self.build(request=changed)

    def test_new_contract_requires_registered_unbound_plan(self):
        changed = copy.deepcopy(self.row)
        changed["status"] = "completed"
        with self.assertRaises(ValueError):
            self.build(source_row=changed)

        changed = copy.deepcopy(self.row)
        changed["provider_collection"] = {
            "bound_at": self.now.isoformat(),
        }
        with self.assertRaises(ValueError):
            self.build(source_row=changed)

    def test_later_binding_preserves_existing_contract(self):
        saved = self.build()
        changed = copy.deepcopy(self.row)
        changed["provider_collection"] = {
            "bound_at": (
                self.now + timedelta(seconds=1)
            ).isoformat(),
        }
        self.assertEqual(
            self.verify(saved, source_row=changed),
            saved["contract"],
        )

    def test_binding_at_contract_creation_is_rejected(self):
        saved = self.build()
        changed = copy.deepcopy(self.row)
        changed["provider_collection"] = {
            "bound_at": self.now.isoformat(),
        }
        with self.assertRaises(ValueError):
            self.verify(saved, source_row=changed)

    def test_changed_origin_is_rejected(self):
        saved = self.build()
        changed = copy.deepcopy(self.request)
        changed["completed_at"] = (
            self.now - timedelta(minutes=3)
        ).isoformat()
        with self.assertRaises(ValueError):
            self.verify(saved, request=changed)

    def test_changed_saved_proposal_is_rejected(self):
        changed = copy.deepcopy(self.request)
        changed["proposal"]["rationale"] = "Changed after completion."
        with self.assertRaises(ValueError):
            self.build(request=changed)

    def test_rehashed_changed_design_is_rejected(self):
        changed = copy.deepcopy(self.request)
        design = changed["research_design"]
        design["hypothesis"] = "A different research question."
        design.pop("design_sha256")
        design["design_sha256"] = queue._digest(design)
        with self.assertRaises(ValueError):
            self.build(request=changed)

    def test_retained_hash_rejects_rehashed_terms(self):
        original = self.build()
        changed = copy.deepcopy(original)
        changed["contract"]["comparison_criteria"][
            "minimum_completed_trades_per_account"
        ] = 1
        changed["sha256"] = queue._digest(changed["contract"])
        with self.assertRaises(ValueError):
            self.verify(
                changed, expected_sha256=original["sha256"]
            )

    def test_reconstruction_rejects_rehashed_terms(self):
        saved = self.build()
        saved["contract"]["comparison_criteria"][
            "minimum_intervention_profit_factor"
        ] = "0.1"
        saved["sha256"] = queue._digest(saved["contract"])
        with self.assertRaises(ValueError):
            self.verify(saved)

    def test_extra_envelope_field_is_rejected(self):
        saved = self.build()
        saved["approved"] = True
        with self.assertRaises(ValueError):
            self.verify(saved)

    def test_inputs_and_verified_result_are_independent(self):
        before_row = copy.deepcopy(self.row)
        before_request = copy.deepcopy(self.request)
        saved = self.build()
        verified = self.verify(saved)
        verified["research_design"]["hypothesis"] = "Changed locally."
        self.assertEqual(self.row, before_row)
        self.assertEqual(self.request, before_request)
        self.assertNotEqual(verified, saved["contract"])
        self.verify(saved)

    def test_contract_grants_no_authority(self):
        body = self.build()["contract"]
        for field in (
            "registration_verified",
            "strategy_change_authorized",
            "promotion_authorized",
            "live_capital_authorized",
        ):
            self.assertIs(body[field], False)


if __name__ == "__main__":
    unittest.main()
