"""Tests for the read-only portfolio intelligence API."""

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.capital import portfolio_intelligence_api as api


PATH = "/portfolio-intelligence"


def response():
    return {
        "schema_version": 1,
        "status": "partial",
        "snapshot_at": "2026-09-21T12:00:00+00:00",
        "valuations": {"portfolios": []},
        "concentration": {
            "combined": {
                "status": "unavailable",
                "concentration": None,
            },
        },
        "historical_metrics": {
            "correlation": {
                "status": "unavailable",
                "reasons": ["No eligible daily-return series."],
            },
        },
        "database_writes": False,
        "execution_authorized": False,
        "allocation_authority": False,
        "live_capital_authority": False,
        "human_approval_required": True,
    }


class IntelligenceApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(api.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

        service = patch.object(
            api, "get_portfolio_intelligence", return_value=response()
        )
        logging = patch.object(api.logger, "exception")

        self.service = service.start()
        self.logging = logging.start()
        self.addCleanup(service.stop)
        self.addCleanup(logging.stop)

    def test_get_returns_service_report(self):
        result = self.client.get(PATH)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), response())
        self.service.assert_called_once_with()

    def test_unavailable_metrics_remain_explicit(self):
        result = self.client.get(PATH).json()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(
            result["historical_metrics"]["correlation"]["status"],
            "unavailable",
        )
        self.assertIsNone(
            result["concentration"]["combined"]["concentration"]
        )

    def test_mutating_methods_are_not_allowed(self):
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                result = getattr(self.client, method)(PATH)
                self.assertEqual(result.status_code, 405)
        self.service.assert_not_called()

    def test_no_approval_creation_or_activation_routes(self):
        for action in ("approve", "create", "activate"):
            with self.subTest(action=action):
                result = self.client.post(f"{PATH}/{action}")
                self.assertEqual(result.status_code, 404)
        self.service.assert_not_called()

    def test_expected_failures_return_sanitized_503(self):
        errors = (
            ValueError("private validation detail"),
            KeyError("private record identifier"),
            RuntimeError("private filesystem path"),
            SQLAlchemyError("private connection detail"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.service.side_effect = error
                result = self.client.get(PATH)
                self.assertEqual(result.status_code, 503)
                self.assertEqual(
                    result.json(),
                    {
                        "detail": (
                            "Portfolio intelligence is currently unavailable. "
                            "Required data could not be read or validated."
                        ),
                    },
                )
                self.assertNotIn("private", result.text)
        self.assertEqual(self.logging.call_count, len(errors))

    def test_openapi_exposes_only_get_for_this_path(self):
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(set(schema["paths"]), {PATH})
        self.assertEqual(set(schema["paths"][PATH]), {"get"})
        self.service.assert_not_called()

    def test_authority_boundaries_are_preserved(self):
        result = self.client.get(PATH).json()
        for field in (
            "database_writes",
            "execution_authorized",
            "allocation_authority",
            "live_capital_authority",
        ):
            with self.subTest(field=field):
                self.assertFalse(result[field])
        self.assertTrue(result["human_approval_required"])


if __name__ == "__main__":
    unittest.main()
