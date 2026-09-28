"""Review retry and evidence-binding tests using temporary research state."""

from copy import deepcopy
import unittest

import test_capital_autonomy_research_draft as fixtures

from app.capital import research_store
from app.capital.research_models import ResearchVerdict
from app.capital.research_workflow import evaluate_research_candidate
from app.capital.validation_plan import research_snapshot


class CapitalReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CapitalResearchDraftTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.expected = research_snapshot(self.fixture.parent)

        with research_store.locked_research_state(write=True) as state:
            row = state["candidates"]["research_parent"]
            row["status"] = "researching"
            row["verdict"] = "pending"

    def review(self, **overrides):
        arguments = {
            "research_id": "research_parent",
            "verdict": ResearchVerdict.INCONCLUSIVE,
            "evidence": ["Verified registered validation packet"],
            "concerns": ["Completed-trade sample below minimum"],
            "evaluation_notes": "Automatic review under Capital paper policy.",
            "expected_research_snapshot": deepcopy(self.expected),
            "request_key": "capital-review:validation-test",
        }
        arguments.update(overrides)
        return evaluate_research_candidate(**arguments)

    def current(self):
        return self.fixture.read()["candidates"]["research_parent"]

    def test_identical_retry_does_not_duplicate_history(self):
        first = self.review()
        second = self.review()
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(len(self.current()["review_history"]), 1)
        self.assertEqual(self.current()["status"], "revision_required")

    def test_conflicting_request_is_rejected(self):
        self.review()
        with self.assertRaises(ValueError):
            self.review(evaluation_notes="Different decision")
        self.assertEqual(len(self.current()["review_history"]), 1)

    def test_changed_hypothesis_blocks_review(self):
        with research_store.locked_research_state(write=True) as state:
            state["candidates"]["research_parent"]["hypothesis"] = "Changed"
        with self.assertRaises(ValueError):
            self.review()
        self.assertEqual(self.current()["status"], "researching")
        self.assertNotIn("review_requests", self.fixture.read())

    def test_changed_criteria_block_review(self):
        with research_store.locked_research_state(write=True) as state:
            state["candidates"]["research_parent"]["success_criteria"] = [
                "Changed criteria"
            ]
        with self.assertRaises(ValueError):
            self.review()
        self.assertEqual(self.current()["review_history"], [])

    def test_retry_preserves_later_candidate_state(self):
        first = self.review()
        with research_store.locked_research_state(write=True) as state:
            row = state["candidates"]["research_parent"]
            row["status"] = "archived"
            row["concerns"].append("Later finding")
        self.assertEqual(self.review().to_dict(), first.to_dict())
        self.assertEqual(self.current()["status"], "archived")
        self.assertIn("Later finding", self.current()["concerns"])

    def test_validation_evidence_is_preserved(self):
        before = self.current()
        self.review()
        after = self.current()
        for field in ("validation_assessments", "validation_recommendations"):
            self.assertEqual(after[field], before[field])

    def test_missing_candidate_is_not_recreated_on_retry(self):
        self.review()
        with research_store.locked_research_state(write=True) as state:
            del state["candidates"]["research_parent"]
        with self.assertRaises(RuntimeError):
            self.review()

    def test_legacy_review_needs_no_new_arguments(self):
        result = self.review(
            request_key=None,
            expected_research_snapshot=None,
        )
        self.assertEqual(result.status.value, "revision_required")
        self.assertNotIn("review_requests", self.fixture.read())

    def test_invalid_keys_do_not_change_candidate(self):
        before = self.current()
        for key in ("", " ", True, 123, "x" * 201):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.review(request_key=key)
        self.assertEqual(self.current(), before)

    def test_promising_review_still_requires_evidence(self):
        with self.assertRaises(ValueError):
            self.review(verdict=ResearchVerdict.PROMISING, evidence=[])
        self.assertEqual(self.current()["status"], "researching")


if __name__ == "__main__":
    unittest.main()
