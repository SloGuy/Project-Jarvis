"""Factory API contract tests; all state access is mocked."""
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.capital import experiment_factory_api as api


class FactoryAPITests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(api.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

        self.submit = self.mock("submit_factory_request")
        self.status = self.mock("get_factory_request")
        self.review = self.mock("build_factory_review")
        self.data = {
            "request_key": "agent:request-1",
            "research_id": "research_test",
            "requested_by": "research-agent",
        }
        self.result = {
            **self.data,
            "status": "awaiting_review",
            "portfolio_id": None,
            "execution_authorized": False,
            "live_capital_authorized": False,
        }
        self.submit.return_value = self.result
        self.status.return_value = self.result
        self.review.return_value = {
            "eligible_for_operator_review": False,
            "blockers": ["Validation has not passed."],
        }
        self.base = "/experiment-factory"
        self.url = f"{self.base}/agent:request-1"

    def mock(self, name):
        handle = patch.object(api, name)
        result = handle.start()
        self.addCleanup(handle.stop)
        return result

    def test_submission_uses_validated_contract(self):
        response = self.client.post(self.base, json=self.data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.result)
        request = self.submit.call_args.args[0]
        self.assertEqual(request.model_dump(), self.data)

    def test_requester_cannot_supply_approval(self):
        response = self.client.post(
            self.base, json={**self.data, "approved": True}
        )
        self.assertEqual(response.status_code, 422)
        self.submit.assert_not_called()

    def test_status_lookup(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.result)
        self.status.assert_called_once_with("agent:request-1")

    def test_review_exposes_blockers_without_authority(self):
        response = self.client.get(f"{self.url}/review")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(
            body["current_eligibility"]["eligible_for_operator_review"]
        )
        self.assertTrue(body["human_approval_required"])
        for field in (
            "creation_authorized", "execution_authorized",
            "live_capital_authorized",
        ):
            self.assertIs(body[field], False)
        self.review.assert_called_once_with("research_test")
        self.submit.assert_not_called()

    def test_missing_request_returns_404(self):
        self.status.side_effect = KeyError("Private internal identifier")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Private", response.text)

    def test_missing_request_prevents_review(self):
        self.status.side_effect = KeyError("missing")
        self.assertEqual(
            self.client.get(f"{self.url}/review").status_code, 404
        )
        self.review.assert_not_called()

    def test_submission_conflict_returns_409(self):
        self.submit.side_effect = ValueError(
            "Request key is already bound to different inputs."
        )
        response = self.client.post(self.base, json=self.data)
        self.assertEqual(response.status_code, 409)

    def test_storage_errors_return_503_without_internal_details(self):
        for error in (
            SQLAlchemyError("Private database details"),
            RuntimeError("Private registry details"),
            OSError("Private file path"),
        ):
            with self.subTest(error=type(error).__name__):
                self.status.side_effect = error
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {
                    "detail": "Factory state or evidence is currently unavailable."
                })

    def test_no_approval_or_creation_endpoint_exists(self):
        for action in ("approve", "create", "activate"):
            with self.subTest(action=action):
                response = self.client.post(f"{self.url}/{action}", json={})
                self.assertIn(response.status_code, (404, 405))
        self.submit.assert_not_called()

    def test_review_cannot_be_posted(self):
        response = self.client.post(f"{self.url}/review", json={})
        self.assertEqual(response.status_code, 405)
        self.review.assert_not_called()


if __name__ == "__main__":
    unittest.main()
