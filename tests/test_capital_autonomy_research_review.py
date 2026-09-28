"""Automatic review tests with temporary stores and mocked verification."""

from copy import deepcopy
import unittest
from unittest.mock import patch

import test_capital_autonomy_research_draft as research_fixtures
import test_capital_autonomy_validation_registration as registry_fixtures

from app.capital import autonomy_research_review as worker
from app.capital import research_store
from app.capital.autonomy_policy import (
    CapitalOperatingPolicy,
    CapitalPolicyError,
)
from app.capital.validation_plan import research_snapshot


class CapitalResearchReviewTests(unittest.TestCase):
    def setUp(self):
        self.research = research_fixtures.CapitalResearchDraftTests()
        self.addCleanup(self.research.doCleanups)
        self.research.setUp()

        self.registration = (
            registry_fixtures.ValidationRegistrationRetryTests()
        )
        self.addCleanup(self.registration.doCleanups)
        self.registration.setUp()

        draft = deepcopy(self.registration.draft)
        draft["research"] = research_snapshot(self.research.parent)
        self.row = self.registration.register(draft)
        self.plan_id = self.row["plan_id"]

        with research_store.locked_research_state(write=True) as state:
            state["candidates"]["research_parent"]["status"] = "proposed"

        self.verified = {
            "plan_id": self.plan_id,
            "plan_sha256": self.row["registered_sha256"],
            "research": deepcopy(draft["research"]),
            "report_sha256": "a" * 64,
            "assessment_sha256": "b" * 64,
            "assessment": {
                "validation_status": "insufficient_evidence",
                "promotion_authorized": False,
                "insufficient_evidence_reasons": ["Too few trades"],
                "failed_performance_criteria": ["minimum_return_percent"],
                "limitations": ["Selected prospective window"],
            },
        }

        self.verification = self.mock(
            "inspect_completed", return_value=self.verified
        )
        self.policy = self.mock(
            "read_operating_policy",
            return_value=CapitalOperatingPolicy(),
        )

    def mock(self, name, **kwargs):
        mocked = patch.object(worker, name, **kwargs)
        result = mocked.start()
        self.addCleanup(mocked.stop)
        return result

    def review(self):
        return worker.review_validation_outcome(self.plan_id)

    def current(self):
        return self.research.read()["candidates"]["research_parent"]

    def test_insufficient_evidence_requires_revision(self):
        result = self.review()
        self.assertEqual(result["research_status"], "revision_required")
        self.assertEqual(result["research_verdict"], "inconclusive")
        self.assertIn("Too few trades", self.current()["concerns"])
        self.assertFalse(result["promotion_authorized"])

    def test_pass_marks_research_ready_without_trading_authority(self):
        self.verified["assessment"]["validation_status"] = "pass"
        self.verified["assessment"]["insufficient_evidence_reasons"] = []
        self.verified["assessment"]["failed_performance_criteria"] = []
        result = self.review()
        self.assertEqual(result["research_status"], "ready_for_experiment")
        self.assertEqual(result["research_verdict"], "promising")
        self.assertFalse(result["promotion_authorized"])
        self.assertFalse(result["live_capital_authorized"])

    def test_fail_rejects_research(self):
        self.verified["assessment"]["validation_status"] = "fail"
        result = self.review()
        self.assertEqual(result["research_status"], "rejected")
        self.assertEqual(result["research_verdict"], "unpromising")

    def test_identical_retry_preserves_single_review(self):
        first = self.review()
        self.assertEqual(self.review(), first)
        self.assertEqual(len(self.current()["review_history"]), 1)

    def test_retry_does_not_undo_later_archive(self):
        first = self.review()
        with research_store.locked_research_state(write=True) as state:
            state["candidates"]["research_parent"]["status"] = "archived"
        self.assertEqual(self.review(), first)
        self.assertEqual(self.current()["status"], "archived")

    def test_verification_failure_changes_no_research(self):
        before = self.research.read()
        self.verification.side_effect = ValueError("Packet hash changed")
        with self.assertRaises(ValueError):
            self.review()
        self.assertEqual(self.research.read(), before)

    def test_changed_hypothesis_is_not_reviewed(self):
        self.verified["research"]["hypothesis"] = "Different hypothesis"
        with self.assertRaises(ValueError):
            self.review()
        self.assertEqual(self.current()["status"], "proposed")

    def test_existing_unrelated_review_is_not_overwritten(self):
        with research_store.locked_research_state(write=True) as state:
            state["candidates"]["research_parent"]["status"] = (
                "revision_required"
            )
        with self.assertRaises(ValueError):
            self.review()
        self.assertNotIn("review_requests", self.research.read())

    def test_pause_blocks_review(self):
        self.policy.return_value = CapitalOperatingPolicy(paused=True)
        with self.assertRaises(CapitalPolicyError):
            self.review()
        self.verification.assert_not_called()

    def test_interrupted_transition_can_resume(self):
        with patch.object(
            worker,
            "evaluate_research_candidate",
            side_effect=RuntimeError("Interrupted before review"),
        ):
            with self.assertRaises(RuntimeError):
                self.review()
        self.assertEqual(self.current()["status"], "researching")
        self.assertEqual(self.review()["research_status"], "revision_required")
        self.assertEqual(len(self.current()["review_history"]), 1)

    def test_original_validation_evidence_is_preserved(self):
        before = self.current()
        self.review()
        after = self.current()
        for field in ("validation_assessments", "validation_recommendations"):
            self.assertEqual(after[field], before[field])
        self.assertIn("capital.lifecycle", after["evaluation_notes"])
        self.assertIn("policy_version", after["evaluation_notes"])


if __name__ == "__main__":
    unittest.main()
