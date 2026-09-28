"""Isolated draft persistence tests; no real model requests."""

from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.capital import research_store
from app.capital.autonomy_policy import (
    CapitalOperatingPolicy,
    CapitalPolicyError,
)
from app.capital.autonomy_research_draft import prepare_revision_draft
from app.capital.research_models import (
    ResearchCandidate,
    ResearchStatus,
    ResearchVerdict,
)


MODULE = "app.capital.autonomy_research_draft"


class CapitalResearchDraftTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)

        for name, value in (
            ("STATE_DIRECTORY", root),
            ("RESEARCH_STATE_FILE", root / "research.json"),
            ("RESEARCH_LOCK_FILE", root / "research.lock"),
        ):
            mocked = patch.object(research_store, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)

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
            success_criteria=["Nonnegative returns after costs"],
            status=ResearchStatus.REVISION_REQUIRED,
            verdict=ResearchVerdict.INCONCLUSIVE,
            proposed_by="operator",
            created_at="2026-09-27T00:00:00+00:00",
            updated_at="2026-09-27T00:00:00+00:00",
            concerns=["Insufficient sample"],
            validation_assessments=[{
                "assessment": {
                    "validation_status": "insufficient_evidence",
                },
            }],
        )
        with research_store.locked_research_state(write=True) as state:
            state["candidates"][self.parent.research_id] = self.parent.to_dict()

        self.proposal = {
            "strategy_name": "mean_reversion_v2",
            "hypothesis": "A changed, testable hypothesis",
            "rationale": "Investigate uncertainty without assuming success.",
            "evidence_ids": ["parent-research", "saved-assessment-1"],
            "next_question": "Does the result persist after costs?",
        }
        model_patch = patch(
            f"{MODULE}.propose_research",
            return_value=deepcopy(self.proposal),
        )
        self.model = model_patch.start()
        self.addCleanup(model_patch.stop)

        policy_patch = patch(
            f"{MODULE}.read_operating_policy",
            return_value=CapitalOperatingPolicy(),
        )
        self.policy = policy_patch.start()
        self.addCleanup(policy_patch.stop)

    def prepare(self, **overrides):
        arguments = {
            "task_id": "task_test",
            "parent_research_id": self.parent.research_id,
            "objective": "Investigate the failed attempt.",
        }
        arguments.update(overrides)
        return prepare_revision_draft(**arguments)

    def read(self):
        with research_store.locked_research_state() as state:
            return deepcopy(state)

    def test_draft_is_saved_without_changing_parent(self):
        draft = self.prepare()
        state = self.read()
        self.assertEqual(
            state["candidates"][self.parent.research_id],
            self.parent.to_dict(),
        )
        self.assertEqual(
            state["autonomy_revision_drafts"]["task_test"],
            draft,
        )
        self.assertEqual(len(state["candidates"]), 1)

    def test_retry_reuses_draft_without_model_call(self):
        first = self.prepare()
        second = self.prepare()
        self.assertEqual(first, second)
        self.model.assert_called_once()

    def test_changed_request_is_rejected(self):
        self.prepare()
        with self.assertRaises(ValueError):
            self.prepare(objective="Another objective")
        self.model.assert_called_once()

    def test_parent_snapshot_and_context_are_retained(self):
        draft = self.prepare()
        self.assertEqual(draft["parent_snapshot"], self.parent.to_dict())
        self.assertEqual(draft["proposal"], self.proposal)
        self.assertEqual(
            [item["id"] for item in draft["model_inputs"]],
            ["parent-research", "saved-assessment-1", "revision-constraints"],
        )
        self.assertFalse(draft["evidence_reverified"])
        self.assertFalse(draft["promotion_authorized"])
        self.assertFalse(draft["live_capital_authorized"])

    def test_model_failure_saves_no_draft(self):
        self.model.side_effect = RuntimeError("Model unavailable")
        with self.assertRaises(RuntimeError):
            self.prepare()
        self.assertNotIn("autonomy_revision_drafts", self.read())

    def test_unchanged_hypothesis_is_rejected(self):
        self.model.return_value["hypothesis"] = self.parent.hypothesis
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertNotIn("autonomy_revision_drafts", self.read())

    def test_parent_change_during_model_call_rejects_draft(self):
        def change_parent(**kwargs):
            with research_store.locked_research_state(write=True) as state:
                state["candidates"][self.parent.research_id]["concerns"].append(
                    "New evidence arrived."
                )
            return deepcopy(self.proposal)

        self.model.side_effect = change_parent
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertNotIn("autonomy_revision_drafts", self.read())

    def test_pause_before_request_prevents_model_call(self):
        self.policy.return_value = CapitalOperatingPolicy(paused=True)
        with self.assertRaises(CapitalPolicyError):
            self.prepare()
        self.model.assert_not_called()

    def test_pause_during_request_prevents_persistence(self):
        self.policy.side_effect = [
            CapitalOperatingPolicy(),
            CapitalOperatingPolicy(paused=True),
        ]
        with self.assertRaises(CapitalPolicyError):
            self.prepare()
        self.model.assert_called_once()
        self.assertNotIn("autonomy_revision_drafts", self.read())

    def test_disabled_policy_prevents_request(self):
        self.policy.return_value = CapitalOperatingPolicy(enabled=False)
        with self.assertRaises(CapitalPolicyError):
            self.prepare()
        self.model.assert_not_called()

    def test_ineligible_parent_prevents_model_call(self):
        with research_store.locked_research_state(write=True) as state:
            state["candidates"][self.parent.research_id]["status"] = "proposed"
        with self.assertRaises(ValueError):
            self.prepare()
        self.model.assert_not_called()

    def test_missing_parent_prevents_model_call(self):
        with self.assertRaises(KeyError):
            self.prepare(parent_research_id="missing")
        self.model.assert_not_called()

    def test_invalid_task_identity_prevents_request(self):
        for value in ("", " ", True, "x" * 161):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.prepare(task_id=value)
        self.model.assert_not_called()

    def test_returned_draft_cannot_modify_saved_record(self):
        draft = self.prepare()
        draft["proposal"]["hypothesis"] = "Changed outside store"
        saved = self.read()["autonomy_revision_drafts"]["task_test"]
        self.assertEqual(
            saved["proposal"]["hypothesis"],
            self.proposal["hypothesis"],
        )



    def apply(self):
        from app.capital.autonomy_research_draft import apply_revision_draft
        return apply_revision_draft(task_id="task_test")

    def test_apply_creates_revision_once(self):
        self.prepare()
        first = self.apply()
        self.assertEqual(self.apply(), first)
        self.assertEqual(len(self.read()["candidates"]), 2)
        self.model.assert_called_once()
        self.assertEqual(first["candidate"]["status"], "proposed")
        self.assertEqual(first["candidate"]["verdict"], "pending")
        self.assertEqual(first["candidate"]["proposed_by"], "capital.research")
        self.assertFalse(first["promotion_authorized"])
        self.assertFalse(first["live_capital_authorized"])

    def test_apply_retry_preserves_later_review(self):
        self.prepare()
        first = self.apply()
        with research_store.locked_research_state(write=True) as state:
            state["candidates"][first["research_id"]]["status"] = "researching"
        self.assertEqual(self.apply(), first)
        self.assertEqual(
            self.read()["candidates"][first["research_id"]]["status"],
            "researching",
        )


    def test_apply_rejects_changed_parent(self):
        self.prepare()
        with research_store.locked_research_state(write=True) as state:
            state["candidates"][self.parent.research_id]["concerns"].append(
                "New evidence."
            )
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(len(self.read()["candidates"]), 1)
        self.assertNotIn("revision_requests", self.read())

    def test_apply_requires_saved_draft(self):
        with self.assertRaises(KeyError):
            self.apply()
        self.model.assert_not_called()

    def test_apply_respects_pause(self):
        self.prepare()
        self.policy.return_value = CapitalOperatingPolicy(paused=True)
        with self.assertRaises(CapitalPolicyError):
            self.apply()
        self.assertEqual(len(self.read()["candidates"]), 1)

    def test_apply_rejects_changed_strategy(self):
        self.prepare()
        with research_store.locked_research_state(write=True) as state:
            draft = state["autonomy_revision_drafts"]["task_test"]
            draft["proposal"]["strategy_name"] = "another_strategy"
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(len(self.read()["candidates"]), 1)

if __name__ == "__main__":
    unittest.main()
