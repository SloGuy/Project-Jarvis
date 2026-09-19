"""HTTP contract tests; no real research or validation stores are accessed."""
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.capital.research_api import router


class ValidationRecommendationAPITests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.url = "/research/candidate-a/validations/plan-a/recommendation"

        self.candidate = self.start_patch(
            "app.capital.research_api.require_research_candidate"
        )
        self.plan = self.start_patch(
            "app.capital.validation_registry.get_plan"
        )
        self.inspect = self.start_patch(
            "app.capital.validation_research.inspect_recommendation"
        )
        self.plan.return_value = {
            "envelope": {"plan": {"research": {"research_id": "candidate-a"}}}
        }

    def start_patch(self, target):
        handle = patch(target)
        mocked = handle.start()
        self.addCleanup(handle.stop)
        return mocked

    def test_returns_verified_recommendation(self):
        expected = {
            "plan_id": "plan-a",
            "validation_status": "insufficient_evidence",
            "recommendation": "REVISE",
            "research_verdict": "inconclusive",
            "promotion_authorized": False,
            "human_approval_required": True,
        }
        self.inspect.return_value = expected

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        self.candidate.assert_called_once_with(research_id="candidate-a")
        self.plan.assert_called_once_with("plan-a")
        self.inspect.assert_called_once_with("plan-a")

    def test_other_candidates_plan_is_rejected_before_verification(self):
        self.plan.return_value["envelope"]["plan"]["research"][
            "research_id"
        ] = "candidate-b"

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)
        self.inspect.assert_not_called()

    def test_missing_candidate(self):
        self.candidate.side_effect = KeyError("missing")

        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.plan.assert_not_called()
        self.inspect.assert_not_called()

    def test_missing_plan(self):
        self.plan.side_effect = KeyError("missing")

        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.inspect.assert_not_called()

    def test_verification_errors_block_recommendation(self):
        for error in (
            ValueError("Replay configuration mismatch: source_sha256"),
            RuntimeError("Registry unavailable"),
            OSError("Private artifact path"),
        ):
            with self.subTest(error=type(error).__name__):
                self.inspect.side_effect = error
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json(), {
                    "detail": "Validation evidence cannot currently be verified."
                })

    def test_post_is_not_allowed(self):
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.inspect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
