import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.capital.committee_models import (
    CommitteeDecision, GateStatus, GraduationGate,
)
from app.capital.committee_service import evaluate_strategy_committee
from app.capital.research_lineage_gate import build_research_lineage_gate


class LineageGateTests(unittest.TestCase):
    def report(self, provenance, failed=0):
        experiment = SimpleNamespace(
            research_id=None,
            hypothesis_version=None,
            strategy_version=None,
        )
        gates = tuple(
            GraduationGate(
                gate_name=f"existing_{index}",
                status=GateStatus.FAILED,
                actual_value=0, required_value=1, rationale="Synthetic failure",
            )
            for index in range(failed)
        )
        with (
            patch(
                "app.capital.committee_service.build_validation_gate",
                return_value=GraduationGate(
                    gate_name="registered_validation", status=GateStatus.PASSED,
                    actual_value="synthetic", required_value="synthetic",
                    rationale="Synthetic passing validation fixture.",
                ),
            ),
            patch(
                "app.capital.committee_service.require_experiment",
                return_value=experiment,
            ),
            patch(
                "app.capital.committee_service.build_graduation_gates",
                return_value=gates,
            ),
            patch(
                "app.capital.committee_service.get_experiment_provenance",
                return_value=provenance,
            ) as lookup,
        ):
            result = evaluate_strategy_committee(
                strategy_performance={
                    "experiment_id": "synthetic",
                    "strategy_name": "synthetic",
                    "portfolio_id": 1,
                }
            )
            lookup.assert_called_once_with(experiment)
            return result

    def test_missing_and_mismatched_block_promotion(self):
        for status in ("unlinked", "mismatch", "unknown"):
            with self.subTest(status=status):
                report = self.report({
                    "status": status,
                    "reasons": ["Synthetic unresolved lineage."],
                })
                self.assertEqual(report.decision, CommitteeDecision.CONTINUE)
                self.assertFalse(report.graduation_eligible)
                self.assertEqual(report.pending_gate_count, 1)
                self.assertEqual(report.failed_gate_count, 0)
                self.assertTrue(any(
                    "research_lineage" in concern
                    for concern in report.concerns
                ))
                serialized = report.to_dict()
                self.assertEqual(
                    next(g for g in serialized["gate_results"] if g["gate_name"] == "research_lineage")["status"], "pending"
                )
                self.assertFalse(serialized["live_capital_authorized"])

    def test_matched_passes_lineage_only(self):
        report = self.report({
            "status": "matched", "reasons": [],
            "launch_snapshot_verified": False,
        })
        self.assertEqual(report.decision, CommitteeDecision.PROMOTE)
        self.assertTrue(report.graduation_eligible)
        self.assertEqual(report.passed_gate_count, 2)
        self.assertTrue(report.human_approval_required)
        self.assertFalse(report.live_capital_authorized)
        self.assertIn(
            "does not verify", next(g for g in report.gate_results if g.gate_name == "research_lineage").rationale
        )

    def test_performance_failures_keep_priority(self):
        for failures, decision in (
            (1, CommitteeDecision.REVISE),
            (3, CommitteeDecision.KILL),
        ):
            report = self.report({"status": "unlinked"}, failed=failures)
            self.assertEqual(report.decision, decision)
            self.assertFalse(report.graduation_eligible)
            self.assertEqual(report.failed_gate_count, failures)
            self.assertEqual(report.pending_gate_count, 1)

    def test_unknown_status_does_not_pass(self):
        gate = build_research_lineage_gate({})
        self.assertEqual(gate.status, GateStatus.PENDING)


if __name__ == "__main__":
    unittest.main()
