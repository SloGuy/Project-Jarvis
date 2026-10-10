import copy
import unittest
from unittest.mock import patch

import test_autonomy_trade_research as fixtures

from app.capital import autonomy_trade_research as queue
from app.capital import research_store
from app.capital import trade_research_runner as runner


class TradeResearchRunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TradeResearchQueueTests(
            methodName="test_mature_failure_is_persisted"
        )
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.request_id = self.fixture.run_queue()["request"]["request_id"]
        self.proposal = {
            "strategy_name": "mean_reversion_v2",
            "hypothesis": (
                "Can a predefined entry filter improve prospective "
                "cost-adjusted performance while preserving acceptance criteria?"
            ),
            "rationale": (
                "The journals show losses, but price paths are needed "
                "to test a causal explanation."
            ),
            "evidence_ids": ["trade-diagnosis"],
            "next_question": "Were eligible quotes available before the losses?",
        }
        from app.capital.trade_research_design import HYPOTHESIS
        self.proposal["hypothesis"] = HYPOTHESIS
        patcher = patch.object(
            runner, "propose_for_request",
            return_value=self.proposal,
        )
        self.model = patcher.start()
        self.addCleanup(patcher.stop)

    def run_worker(self):
        return runner.process_trade_research_once()

    def saved(self):
        return self.fixture.state()[queue.STORE_KEY][self.request_id]

    def test_proposal_is_saved_with_inputs_and_hash(self):
        result = self.run_worker()
        saved = self.saved()
        self.assertEqual(result["status"], "advisory_proposal_saved")
        self.assertEqual(result["processed_count"], 1)
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["proposal"], self.proposal)
        self.assertEqual(
            saved["proposal_sha256"], queue._digest(self.proposal)
        )
        self.assertEqual(saved["model_inputs"][0]["id"], "trade-diagnosis")
        self.assertFalse(saved["proposal_scientifically_validated"])
        self.model.assert_called_once()

    def test_completed_request_does_not_repeat_model_call(self):
        self.run_worker()
        second = self.run_worker()
        self.assertEqual(second["status"], "idle")
        self.assertEqual(second["processed_count"], 0)
        self.model.assert_called_once()

    def test_model_failure_is_recorded_without_raw_error_text(self):
        self.model.side_effect = RuntimeError("sensitive model URL")
        with self.assertRaises(RuntimeError):
            self.run_worker()
        saved = self.saved()
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["error_type"], "RuntimeError")
        self.assertNotIn("sensitive model URL", queue._canonical(saved))
        self.assertNotIn("proposal", saved)
        self.assertEqual(self.run_worker()["status"], "idle")
        self.model.assert_called_once()

    def test_wrong_strategy_is_rejected(self):
        self.proposal["strategy_name"] = "momentum_alignment_v1"
        with self.assertRaisesRegex(ValueError, "changed the experiment"):
            self.run_worker()
        self.assertEqual(self.saved()["status"], "failed")
        self.assertNotIn("proposal", self.saved())

    def test_unknown_evidence_is_rejected(self):
        self.proposal["evidence_ids"] = ["invented-evidence"]
        with self.assertRaisesRegex(ValueError, "evidence citation"):
            self.run_worker()
        self.assertEqual(self.saved()["status"], "failed")

    def test_extra_proposal_fields_are_rejected(self):
        self.proposal["execute_trade"] = True
        with self.assertRaisesRegex(ValueError, "proposal structure"):
            self.run_worker()
        self.assertEqual(self.saved()["status"], "failed")

    def test_configuration_change_before_claim_prevents_model_call(self):
        self.fixture.current.return_value = {"changed": True}
        with self.assertRaisesRegex(ValueError, "before proposal"):
            self.run_worker()
        self.assertEqual(self.saved()["status"], "queued")
        self.model.assert_not_called()

    def test_configuration_change_during_generation_records_failure(self):
        original = copy.deepcopy(self.fixture.current.return_value)
        self.fixture.current.side_effect = [original, {"changed": True}]
        with self.assertRaisesRegex(ValueError, "during proposal"):
            self.run_worker()
        self.assertEqual(self.saved()["status"], "failed")
        self.assertNotIn("proposal", self.saved())

    def test_authority_denial_before_selection_prevents_model_call(self):
        self.fixture.authority.side_effect = PermissionError("paused")
        with self.assertRaises(PermissionError):
            self.run_worker()
        self.assertEqual(self.saved()["status"], "queued")
        self.model.assert_not_called()

    def test_authority_denial_after_model_prevents_proposal_save(self):
        def generate(request, evidence):
            self.fixture.authority.side_effect = PermissionError("paused")
            return self.proposal

        self.model.side_effect = generate
        with self.assertRaises(PermissionError):
            self.run_worker()
        self.assertEqual(self.saved()["status"], "failed")
        self.assertNotIn("proposal", self.saved())

    def test_interrupted_running_request_is_visibly_blocked(self):
        with research_store.locked_research_state(write=True) as state:
            request = state[queue.STORE_KEY][self.request_id]
            request["status"] = "running"
            request["run_token"] = "interrupted-attempt"
        result = self.run_worker()
        self.assertEqual(result["status"], "blocked_running_request")
        self.assertEqual(result["request_ids"], [self.request_id])
        self.model.assert_not_called()

    def test_changed_ownership_is_not_overwritten(self):
        def generate(request, evidence):
            with research_store.locked_research_state(write=True) as state:
                state[queue.STORE_KEY][self.request_id]["run_token"] = "new-owner"
            return self.proposal

        self.model.side_effect = generate
        with self.assertRaisesRegex(ValueError, "ownership or inputs changed"):
            self.run_worker()
        self.assertEqual(self.saved()["run_token"], "new-owner")
        self.assertEqual(self.saved()["status"], "running")
        self.assertNotIn("proposal", self.saved())

    def test_existing_research_is_unchanged(self):
        self.run_worker()
        self.assertEqual(
            self.fixture.state()["candidates"],
            self.fixture.candidates_before,
        )

    def test_model_summary_preserves_scope_and_thresholds(self):
        self.run_worker()
        request, evidence = self.model.call_args.args
        self.assertEqual(request["request_id"], self.request_id)
        self.assertLessEqual(len(evidence[0]["summary"]), 4000)
        import json
        payload = json.loads(evidence[0]["summary"])
        self.assertFalse(payload["validation_verified"])
        self.assertEqual(
            payload["thresholds"], self.fixture.diagnosis["thresholds"]
        )
        self.assertIn("limitations", payload)
        self.assertIn("omitted_group_counts", payload)

    def test_result_grants_no_strategy_or_live_authority(self):
        result = self.run_worker()
        for field in (
            "research_candidate_changed",
            "strategy_change_authorized",
            "live_capital_authorized",
        ):
            self.assertIs(result[field], False)


if __name__ == "__main__":
    unittest.main()
