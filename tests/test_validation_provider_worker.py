"""Test worker selection and evaluation handoff for provider plans."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.capital import autonomy_validation_evaluation as evaluation
from app.capital import autonomy_validation_worker as worker
from app.capital import validation_provider_collection as collection
from app.capital.autonomy_validation_registration import PREFIX


@contextmanager
def research_state():
    yield {"candidates": {}, "review_requests": {}}


class ProviderWorkerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(
            2026, 10, 7, 12, 0, tzinfo=timezone.utc
        )
        self.row = {
            "plan_id": "validation_worker_fixture",
            "registered_sha256": "a" * 64,
            "status": "registered",
            "envelope": {
                "plan": {
                    "schema_version": 2,
                    "created_by": PREFIX + "fixture",
                    "end_exclusive": (
                        self.now - timedelta(minutes=1)
                    ).isoformat(),
                    "research": {
                        "research_id": "research_worker_fixture",
                        "hypothesis_version": 1,
                    },
                }
            },
            "provider_collection": {
                "status": "sealed",
            },
        }

        self.authorize = self.start_patch(
            evaluation, "_authorize"
        )
        self.get_plan = self.start_patch(
            evaluation.registry,
            "get_plan",
            return_value=deepcopy(self.row),
        )
        self.start_patch(
            evaluation.registry,
            "now_utc",
            return_value=self.now,
        )
        self.execute = self.start_patch(
            evaluation, "execute_registered"
        )
        self.attach = self.start_patch(
            evaluation,
            "attach_completed",
            return_value={
                "research": {
                    "research_id": "research_worker_fixture",
                },
                "assessment": {
                    "validation_status": "insufficient_evidence",
                },
            },
        )
        self.recommend = self.start_patch(
            evaluation,
            "record_recommendation",
            return_value={"recommendation": "REVISE"},
        )
        self.validate_provider = self.start_patch(
            collection,
            "validate_provider_collection",
            return_value={"status": "sealed"},
        )
        self.materialize = self.start_patch(
            collection,
            "materialize_provider_collection",
            return_value={"collection": {}, "receipts": []},
        )

    def start_patch(self, target, attribute, **arguments):
        pending = patch.object(target, attribute, **arguments)
        result = pending.start()
        self.addCleanup(pending.stop)
        return result

    def prepare_completion(self):
        completed = deepcopy(self.row)
        completed["status"] = "completed"
        self.get_plan.side_effect = [
            deepcopy(self.row),
            completed,
        ]

    def process(self):
        return evaluation._process(self.row["plan_id"])

    def select(self, row=None):
        selected = deepcopy(self.row if row is None else row)

        @contextmanager
        def registry_state():
            yield {"plans": {selected["plan_id"]: selected}}

        with (
            patch.object(
                worker,
                "read_operating_policy",
                return_value=SimpleNamespace(
                    enabled=True, paused=False
                ),
            ),
            patch.object(
                worker.registry,
                "locked_state",
                registry_state,
            ),
            patch.object(
                worker,
                "locked_research_state",
                research_state,
            ),
            patch.object(
                worker,
                "process_validation",
                return_value={"status": "dispatched"},
            ) as dispatch,
        ):
            result = worker._cycle()

        return result, dispatch

    def test_sealed_provider_collection_advances_to_outcome(self):
        self.prepare_completion()

        result = self.process()

        self.validate_provider.assert_called_once()
        self.materialize.assert_called_once()
        self.execute.assert_called_once_with(self.row["plan_id"])
        self.attach.assert_called_once()
        self.recommend.assert_called_once()
        self.assertEqual(result["status"], "outcome_recorded")
        self.assertFalse(result["promotion_authorized"])
        self.assertFalse(result["live_capital_authorized"])

    def test_unsealed_provider_collection_waits(self):
        self.validate_provider.return_value = {
            "status": "collecting",
        }

        result = self.process()

        self.assertEqual(result["status"], "waiting_for_seal")
        self.materialize.assert_not_called()
        self.execute.assert_not_called()

    def test_collection_validation_failure_prevents_execution(self):
        self.validate_provider.side_effect = ValueError(
            "Collection mismatch."
        )

        with self.assertRaises(ValueError):
            self.process()

        self.execute.assert_not_called()

    def test_chain_failure_prevents_execution(self):
        self.materialize.side_effect = ValueError(
            "Chain mismatch."
        )

        with self.assertRaises(ValueError):
            self.process()

        self.execute.assert_not_called()

    def test_before_deadline_waits_without_materialization(self):
        changed = deepcopy(self.row)
        changed["envelope"]["plan"]["end_exclusive"] = (
            self.now + timedelta(minutes=1)
        ).isoformat()
        self.get_plan.return_value = changed

        result = self.process()

        self.assertEqual(result["status"], "waiting_for_deadline")
        self.validate_provider.assert_not_called()
        self.execute.assert_not_called()

    def test_running_and_failed_plans_are_not_rerun(self):
        for status in ("running", "failed"):
            with self.subTest(status=status):
                changed = deepcopy(self.row)
                changed["status"] = status
                self.get_plan.return_value = changed

                self.assertEqual(
                    self.process()["status"], "blocked"
                )

        self.execute.assert_not_called()

    def test_completed_plan_records_outcome_without_rerun(self):
        changed = deepcopy(self.row)
        changed["status"] = "completed"
        self.get_plan.return_value = changed

        self.assertEqual(
            self.process()["status"], "outcome_recorded"
        )
        self.execute.assert_not_called()

    def test_execution_authority_is_checked(self):
        self.prepare_completion()
        self.process()

        actions = [
            call.args[0]
            for call in self.authorize.call_args_list
        ]
        self.assertEqual(actions, [
            "capital.inspect_evidence",
            "capital.run_validation",
            "capital.assess_validation",
            "capital.assess_validation",
        ])

    def test_worker_selects_sealed_provider_collection(self):
        result, dispatch = self.select()

        self.assertEqual(result["status"], "dispatched")
        dispatch.assert_called_once_with(self.row["plan_id"])

    def test_worker_waits_for_unsealed_provider_collection(self):
        changed = deepcopy(self.row)
        changed["provider_collection"]["status"] = "collecting"

        result, dispatch = self.select(changed)

        self.assertEqual(
            result["status"], "validation_in_progress"
        )
        dispatch.assert_not_called()

    def test_provider_plan_does_not_use_legacy_seal_hint(self):
        changed = deepcopy(self.row)
        changed.pop("provider_collection")
        changed["witness_collection"] = {"status": "sealed"}

        result, dispatch = self.select(changed)

        self.assertEqual(
            result["status"], "validation_in_progress"
        )
        dispatch.assert_not_called()

    def test_legacy_sealed_collection_remains_selectable(self):
        changed = deepcopy(self.row)
        changed["envelope"]["plan"]["schema_version"] = 1
        changed.pop("provider_collection")
        changed["witness_collection"] = {"status": "sealed"}

        result, dispatch = self.select(changed)

        self.assertEqual(result["status"], "dispatched")
        dispatch.assert_called_once_with(changed["plan_id"])

    def test_unowned_plan_is_not_selected(self):
        changed = deepcopy(self.row)
        changed["envelope"]["plan"]["created_by"] = "operator"

        result, dispatch = self.select(changed)

        self.assertEqual(
            result["status"], "no_new_validation_work"
        )
        dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
