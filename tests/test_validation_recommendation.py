"""Isolated tests for advisory validation recommendations."""
from copy import deepcopy
import unittest

from app.capital.validation_recommendation import (
    build_validation_recommendation,
)


class ValidationRecommendationTests(unittest.TestCase):
    def test_outcomes_preserve_evidence_and_deny_authority(self):
        cases = (
            ("pass", "PASS", "promising"),
            ("fail", "REJECT", "unpromising"),
            ("insufficient_evidence", "REVISE", "inconclusive"),
        )
        for status, recommendation, verdict in cases:
            with self.subTest(status=status):
                assessment = {
                    "validation_status": status,
                    "promotion_authorized": False,
                    "measurements": {"completed_trades": 11},
                }
                original = deepcopy(assessment)
                result = build_validation_recommendation(assessment)
                self.assertEqual(result, {
                    "schema_version": 1,
                    "validation_status": status,
                    "recommendation": recommendation,
                    "research_verdict": verdict,
                    "promotion_authorized": False,
                    "human_approval_required": True,
                })
                self.assertEqual(assessment, original)
                self.assertEqual(
                    result, build_validation_recommendation(assessment)
                )

    def test_invalid_status_or_authority_is_rejected(self):
        invalid_inputs = (
            {},
            {"validation_status": "pass"},
            {"validation_status": "pass", "promotion_authorized": True},
            {"validation_status": "pass", "promotion_authorized": 0},
            {"validation_status": "pass", "promotion_authorized": None},
            {"validation_status": "unknown", "promotion_authorized": False},
            {"validation_status": [], "promotion_authorized": False},
        )
        for assessment in invalid_inputs:
            with self.subTest(assessment=assessment):
                with self.assertRaises(ValueError):
                    build_validation_recommendation(assessment)

    def test_insufficient_sample_keeps_failed_checks_and_recommends_revise(self):
        assessment = {
            "validation_status": "insufficient_evidence",
            "promotion_authorized": False,
            "failed_performance_criteria": ["minimum_return_percent"],
            "insufficient_evidence_reasons": ["Trade sample below minimum."],
        }
        original = deepcopy(assessment)
        result = build_validation_recommendation(assessment)
        self.assertEqual(result["recommendation"], "REVISE")
        self.assertEqual(assessment, original)


if __name__ == "__main__":
    unittest.main()
