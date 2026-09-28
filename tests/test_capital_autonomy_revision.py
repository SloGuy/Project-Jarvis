"""Revision retry tests using an isolated research store."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.capital import research_store
from app.capital.research_models import (
    ResearchCandidate,
    ResearchStatus,
    ResearchVerdict,
)
from app.capital.research_revision import revise_research_candidate


class CapitalAutonomyRevisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)

        for name, value in (
            ("STATE_DIRECTORY", root),
            ("RESEARCH_STATE_FILE", root / "research.json"),
            ("RESEARCH_LOCK_FILE", root / "research.lock"),
        ):
            patched = patch.object(research_store, name, value)
            patched.start()
            self.addCleanup(patched.stop)

        self.parent = ResearchCandidate(
            research_id="research_parent",
            strategy_name="mean_reversion_v2",
            display_name="Test research",
            hypothesis="Original hypothesis",
            description="Test description",
            market_regime="unspecified",
            asset_universe=["BTC"],
            data_requirements=["Verified observations"],
            risk_thesis="Losses remain possible.",
            success_criteria=["Meet registered criteria after costs"],
            status=ResearchStatus.REVISION_REQUIRED,
            verdict=ResearchVerdict.INCONCLUSIVE,
            proposed_by="original-operator",
            created_at="2026-09-27T00:00:00+00:00",
            updated_at="2026-09-27T00:00:00+00:00",
            evidence=["Original evidence"],
            concerns=["Insufficient sample"],
            review_history=[{"verdict": "inconclusive"}],
            validation_assessments=[{
                "assessment": {"validation_status": "insufficient_evidence"},
            }],
            validation_recommendations=[{"recommendation": "REVISE"}],
        )
        with research_store.locked_research_state(write=True) as state:
            state["candidates"][self.parent.research_id] = self.parent.to_dict()

    def revise(self, **overrides):
        arguments = {
            "parent_research_id": self.parent.research_id,
            "strategy_name": self.parent.strategy_name,
            "hypothesis": "A new predeclared hypothesis",
            "revision_reason": "Investigate the unresolved evidence.",
            "request_key": "task:test-revision",
            "proposed_by": "capital.research",
        }
        arguments.update(overrides)
        return revise_research_candidate(**arguments)

    def read(self):
        with research_store.locked_research_state() as state:
            return state

    def test_identical_retry_returns_same_result(self):
        first = self.revise()
        second = self.revise()
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(len(self.read()["candidates"]), 2)

    def test_changed_request_is_rejected(self):
        self.revise()
        with self.assertRaises(ValueError):
            self.revise(hypothesis="Different request")
        self.assertEqual(len(self.read()["candidates"]), 2)

    def test_changed_proposer_is_rejected(self):
        self.revise()
        with self.assertRaises(ValueError):
            self.revise(proposed_by="another-agent")

    def test_parent_evidence_is_preserved(self):
        self.revise()
        saved = self.read()["candidates"][self.parent.research_id]
        for field in (
            "evidence",
            "concerns",
            "review_history",
            "validation_assessments",
            "validation_recommendations",
        ):
            self.assertEqual(saved[field], self.parent.to_dict()[field])
        self.assertEqual(saved["status"], "archived")

    def test_child_has_clean_evidence_and_agent_attribution(self):
        child = self.revise()
        self.assertEqual(child.proposed_by, "capital.research")
        self.assertEqual(child.status, ResearchStatus.PROPOSED)
        self.assertEqual(child.verdict, ResearchVerdict.PENDING)
        self.assertEqual(child.parent_research_id, self.parent.research_id)
        self.assertEqual(child.hypothesis_version, 2)
        for field in (
            "evidence",
            "concerns",
            "review_history",
            "evaluation_attachments",
            "validation_assessments",
            "validation_recommendations",
        ):
            self.assertEqual(getattr(child, field), [])

    def test_retry_does_not_reset_later_candidate_state(self):
        first = self.revise()
        with research_store.locked_research_state(write=True) as state:
            row = state["candidates"][first.research_id]
            row["status"] = "researching"
            row["evidence"] = ["Later evidence"]
        retried = self.revise()
        self.assertEqual(retried.to_dict(), first.to_dict())
        current = self.read()["candidates"][first.research_id]
        self.assertEqual(current["status"], "researching")
        self.assertEqual(current["evidence"], ["Later evidence"])

    def test_missing_created_candidate_is_not_recreated(self):
        child = self.revise()
        with research_store.locked_research_state(write=True) as state:
            del state["candidates"][child.research_id]
        with self.assertRaises(RuntimeError):
            self.revise()
        self.assertEqual(len(self.read()["candidates"]), 1)

    def test_failed_revision_records_no_request(self):
        with self.assertRaises(ValueError):
            self.revise(hypothesis=self.parent.hypothesis)
        state = self.read()
        self.assertNotIn("revision_requests", state)
        self.assertEqual(len(state["candidates"]), 1)
        self.assertEqual(
            state["candidates"][self.parent.research_id]["status"],
            "revision_required",
        )

    def test_existing_callers_need_no_request_key_or_proposer(self):
        child = self.revise(request_key=None, proposed_by=None)
        self.assertEqual(child.proposed_by, "original-operator")
        self.assertNotIn("revision_requests", self.read())

    def test_invalid_request_keys_are_rejected(self):
        for value in ("", " ", "x" * 201, True, 123):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.revise(request_key=value)
        self.assertEqual(len(self.read()["candidates"]), 1)

    def test_another_active_candidate_blocks_revision(self):
        with research_store.locked_research_state(write=True) as state:
            other = self.parent.to_dict()
            other["research_id"] = "research_other"
            other["status"] = "proposed"
            state["candidates"]["research_other"] = other
        with self.assertRaises(ValueError):
            self.revise()
        self.assertNotIn("revision_requests", self.read())

    def test_rejected_parent_stays_rejected(self):
        with research_store.locked_research_state(write=True) as state:
            state["candidates"][self.parent.research_id]["status"] = "rejected"
        self.revise()
        self.assertEqual(
            self.read()["candidates"][self.parent.research_id]["status"],
            "rejected",
        )


if __name__ == "__main__":
    unittest.main()
