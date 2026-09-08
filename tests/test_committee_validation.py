from contextlib import nullcontext
from copy import deepcopy
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.capital import committee_validation as validation
from app.capital.committee_models import GateStatus
from app.capital.validation_plan import research_snapshot

fixture = runpy.run_path("tests/test_research_evidence.py")


class CommitteeValidationTests(unittest.TestCase):
    def setUp(self):
        self.candidate = fixture["candidate"]()
        self.experiment = SimpleNamespace(
            research_id=self.candidate.research_id,
            hypothesis_version=self.candidate.hypothesis_version,
            strategy_name=self.candidate.strategy_name,
            strategy_version="2.0",
        )
        self.research = research_snapshot(self.candidate)

    def evaluate(self, outcomes, *, statuses=None, attached=True, corrupted=False):
        plans, records = {}, {}
        self.candidate.validation_assessments = []
        for index, outcome in enumerate(outcomes):
            plan_id = f"plan_{index}"
            plans[plan_id] = {
                "status": statuses[index] if statuses else "completed",
                "envelope": {"plan": {
                    "research": self.research,
                    "strategy_version": "2.0",
                }},
            }
            record = {
                "plan_id": plan_id,
                "research": self.research,
                "plan_sha256": "plan_hash",
                "report_sha256": "report_hash",
                "assessment_sha256": "assessment_hash",
                "assessment": {
                    "validation_status": outcome,
                    "promotion_authorized": False,
                },
            }
            records[plan_id] = record
            if attached:
                self.candidate.validation_assessments.append(deepcopy(record))
        if corrupted:
            self.candidate.validation_assessments[0]["assessment_sha256"] = "changed"
        with (
            patch.object(validation, "get_research_candidate", return_value=self.candidate),
            patch.object(validation, "locked_state",
                         return_value=nullcontext({"plans": plans})),
            patch.object(validation, "inspect_completed",
                         side_effect=lambda plan_id: records[plan_id]),
        ):
            return validation.build_validation_gate(self.experiment)

    def test_no_attempts_and_missing_attachment_pending(self):
        self.assertEqual(self.evaluate([]).status, GateStatus.PENDING)
        self.assertEqual(
            self.evaluate(["pass"], attached=False).status, GateStatus.PENDING
        )

    def test_insufficient_and_unfinished_pending(self):
        self.assertEqual(
            self.evaluate(["insufficient_evidence"]).status, GateStatus.PENDING
        )
        for status in ("registered", "running", "failed"):
            self.assertEqual(
                self.evaluate(["pass"], statuses=[status]).status, GateStatus.PENDING
            )

    def test_verified_failure_not_hidden_by_pass(self):
        self.assertEqual(
            self.evaluate(["pass", "fail"]).status, GateStatus.FAILED
        )

    def test_all_pass_required(self):
        self.assertEqual(self.evaluate(["pass", "pass"]).status, GateStatus.PASSED)
        self.assertEqual(
            self.evaluate(["pass", "insufficient_evidence"]).status,
            GateStatus.PENDING,
        )

    def test_tamper_is_pending_not_measured_failure(self):
        self.assertEqual(
            self.evaluate(["pass"], corrupted=True).status, GateStatus.PENDING
        )

    def test_missing_lineage(self):
        self.experiment.research_id = None
        self.assertEqual(
            validation.build_validation_gate(self.experiment).status,
            GateStatus.PENDING,
        )


if __name__ == "__main__":
    unittest.main()
