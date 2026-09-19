"""Factory review tests; no database or runtime-store access."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.capital import experiment_factory_review as review
from app.capital.research_models import ResearchCandidate, ResearchStatus, ResearchVerdict
from app.capital.validation_plan import research_snapshot


class FactoryReviewTests(unittest.TestCase):
    def setUp(self):
        self.candidate = ResearchCandidate(
            research_id="research_test",
            strategy_name="mean_reversion_v2",
            display_name="Test",
            hypothesis="Test hypothesis",
            description="Test only",
            market_regime="Any",
            asset_universe=["BTC"],
            data_requirements=["Prices"],
            risk_thesis="Test",
            success_criteria=["Test"],
            status=ResearchStatus.READY_FOR_EXPERIMENT,
            verdict=ResearchVerdict.PROMISING,
            proposed_by="test",
            created_at="2026-09-19T00:00:00+00:00",
            updated_at="2026-09-19T00:00:00+00:00",
        )
        self.strategy = SimpleNamespace(
            name="mean_reversion_v2", version="2.0", enabled=True,
            to_dict=lambda: {"name": "mean_reversion_v2", "version": "2.0"},
        )
        self.gate = SimpleNamespace(
            status=SimpleNamespace(value="passed"),
            rationale="Synthetic verified gate",
            actual_value={"attempts": [{"plan_id": "plan-1"}]},
        )
        self.row = {
            "plan_id": "plan-1",
            "status": "completed",
            "registered_sha256": "a" * 64,
            "envelope": {"plan": {"research": research_snapshot(self.candidate)}},
        }
        self.mock("require_research_candidate").return_value = self.candidate
        self.mock("require_strategy").return_value = self.strategy
        self.mock("build_validation_gate").return_value = self.gate
        self.get_plan = self.mock("get_plan")
        self.get_plan.return_value = self.row
        self.binding = self.mock("current_binding")
        self.mock("capture_replay_manifest").return_value = {"fixture": True}

    def mock(self, name):
        handle = patch.object(review, name)
        result = handle.start()
        self.addCleanup(handle.stop)
        return result

    def build(self):
        return review.build_factory_review("research_test")

    def assert_blocked(self):
        result = self.build()
        self.assertFalse(result["eligible_for_operator_review"])
        self.assertTrue(result["blockers"])
        self.assertIsNone(result["proposed_experiment"])
        self.assertEqual(result["verified_plan_bindings"], [])
        self.assertFalse(result["creation_authorized"])
        return result

    def test_eligible_preview_grants_no_authority(self):
        before = deepcopy(self.candidate.to_dict())
        result = self.build()
        self.assertTrue(result["eligible_for_operator_review"])
        self.assertEqual(result["blockers"], [])
        proposed = result["proposed_experiment"]
        self.assertEqual(proposed["status"], "planned")
        self.assertEqual(proposed["execution_mode"], "disabled")
        self.assertEqual(proposed["portfolio_type"], "paper")
        self.assertIs(proposed["portfolio_active"], False)
        self.assertIs(result["human_approval_required"], True)
        for field in (
            "creation_authorized", "execution_authorized",
            "live_capital_authorized",
        ):
            self.assertIs(result[field], False)
        self.binding.assert_called_once_with(self.row["envelope"]["plan"])
        self.assertEqual(self.candidate.to_dict(), before)

    def test_research_review_is_required(self):
        for status, verdict in (
            (ResearchStatus.PROPOSED, ResearchVerdict.PENDING),
            (ResearchStatus.REVISION_REQUIRED, ResearchVerdict.INCONCLUSIVE),
            (ResearchStatus.READY_FOR_EXPERIMENT, ResearchVerdict.PENDING),
        ):
            with self.subTest(status=status, verdict=verdict):
                self.candidate.status = status
                self.candidate.verdict = verdict
                self.assert_blocked()
        self.binding.assert_not_called()

    def test_disabled_strategy_is_blocked(self):
        self.strategy.enabled = False
        self.assert_blocked()
        self.binding.assert_not_called()

    def test_unsupported_strategy_is_blocked(self):
        self.strategy.name = "another_strategy"
        self.assert_blocked()
        self.binding.assert_not_called()

    def test_pending_or_failed_validation_is_blocked(self):
        for status in ("pending", "failed"):
            with self.subTest(status=status):
                self.gate.status.value = status
                self.assert_blocked()
        self.binding.assert_not_called()

    def test_source_mismatch_is_blocked(self):
        self.binding.side_effect = ValueError("Source fingerprint changed")
        self.assert_blocked()

    def test_empty_attempt_list_is_blocked(self):
        self.gate.actual_value["attempts"] = []
        self.assert_blocked()

    def test_unfinished_attempt_is_blocked(self):
        self.row["status"] = "running"
        self.assert_blocked()
        self.binding.assert_not_called()

    def test_changed_research_binding_is_blocked(self):
        self.row["envelope"]["plan"]["research"]["hypothesis"] = "Changed"
        self.assert_blocked()

    def test_every_attempt_binding_is_checked(self):
        second = deepcopy(self.row)
        second["plan_id"] = "plan-2"
        self.gate.actual_value["attempts"].append({"plan_id": "plan-2"})
        self.get_plan.side_effect = [self.row, second]
        self.binding.side_effect = [None, ValueError("Second binding changed")]
        self.assert_blocked()
        self.assertEqual(self.binding.call_count, 2)


if __name__ == "__main__":
    unittest.main()
