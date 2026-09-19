"""Factory submission tests; no production database or research writes."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.capital import experiment_factory_service as service
from app.capital.experiment_factory_models import ExperimentFactoryRequest
from app.capital.experiment_factory_store import ExperimentFactoryRecord
from app.market_db.models import Portfolio


class FactorySubmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "factory.sqlite"
        self.engine = create_engine(f"sqlite:///{path}")
        self.addCleanup(self.engine.dispose)
        Portfolio.__table__.create(self.engine)
        ExperimentFactoryRecord.__table__.create(self.engine)
        self.sessions = sessionmaker(bind=self.engine)

        handle = patch.object(service, "SessionLocal", self.sessions)
        handle.start()
        self.addCleanup(handle.stop)
        handle = patch.object(service, "require_research_candidate")
        self.candidate = handle.start()
        self.addCleanup(handle.stop)
        self.candidate.return_value = SimpleNamespace(
            status=SimpleNamespace(value="proposed")
        )
        self.request = ExperimentFactoryRequest(
            request_key="agent:request-1",
            research_id="research_test",
            requested_by="research-agent",
        )

    def rows(self):
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    ExperimentFactoryRecord.__table__.select()
                ).mappings()
            ]

    def test_status_lookup_preserves_pending_request(self):
        service.submit_factory_request(self.request)
        before = self.rows()

        result = service.get_factory_request(self.request.request_key)

        self.assertEqual(result["status"], "awaiting_review")
        self.assertIsNone(result["experiment"])
        self.assertIsNone(result["portfolio_id"])
        self.assertTrue(result["human_approval_required"])
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["live_capital_authorized"])
        self.assertEqual(self.rows(), before)

    def test_missing_request_status(self):
        with self.assertRaises(KeyError):
            service.get_factory_request("missing")
        self.assertEqual(self.rows(), [])

    def test_submission_creates_only_pending_request(self):
        result = service.submit_factory_request(self.request)
        self.assertEqual(result["status"], "awaiting_review")
        self.assertIsNone(result["portfolio_id"])
        self.assertIs(result["live_capital_authorized"], False)
        self.assertIs(result["execution_authorized"], False)
        self.assertEqual(len(self.rows()), 1)
        with self.sessions() as session:
            self.assertEqual(
                session.scalars(select(Portfolio)).all(), []
            )
        self.candidate.assert_called_once_with(research_id="research_test")

    def test_identical_retry_preserves_record(self):
        first = service.submit_factory_request(self.request)
        before = self.rows()
        self.candidate.reset_mock()
        second = service.submit_factory_request(self.request)
        self.assertEqual(first, second)
        self.assertEqual(self.rows(), before)
        self.candidate.assert_not_called()

    def test_request_key_cannot_change_inputs(self):
        service.submit_factory_request(self.request)
        before = self.rows()
        for changes in (
            {"research_id": "research_other"},
            {"requested_by": "another-agent"},
        ):
            with self.subTest(changes=changes):
                request = ExperimentFactoryRequest(**{
                    **self.request.model_dump(), **changes,
                })
                with self.assertRaisesRegex(ValueError, "different inputs"):
                    service.submit_factory_request(request)
                self.assertEqual(self.rows(), before)

    def test_candidate_cannot_get_second_request(self):
        service.submit_factory_request(self.request)
        before = self.rows()
        request = ExperimentFactoryRequest(**{
            **self.request.model_dump(), "request_key": "agent:request-2",
        })
        with self.assertRaisesRegex(ValueError, "already has"):
            service.submit_factory_request(request)
        self.assertEqual(self.rows(), before)

    def test_unknown_candidate_creates_nothing(self):
        self.candidate.side_effect = KeyError("Unknown candidate")
        with self.assertRaises(KeyError):
            service.submit_factory_request(self.request)
        self.assertEqual(self.rows(), [])

    def test_ineligible_candidate_creates_nothing(self):
        for status in ("archived", "rejected", "revision_required"):
            with self.subTest(status=status):
                self.candidate.return_value.status.value = status
                with self.assertRaisesRegex(ValueError, "not eligible"):
                    service.submit_factory_request(self.request)
                self.assertEqual(self.rows(), [])

    def test_flush_failure_rolls_back(self):
        with patch.object(
            Session, "flush", side_effect=RuntimeError("Synthetic failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "Synthetic failure"):
                service.submit_factory_request(self.request)
        self.assertEqual(self.rows(), [])

    def insert_competing_request(self, request_key):
        with self.sessions.begin() as session:
            session.add(ExperimentFactoryRecord(
                request_key=request_key,
                research_id=self.request.research_id,
                requested_by=self.request.requested_by,
                status="awaiting_review",
            ))

    def collision_lookup(self, competing_key):
        original = service.find_existing
        calls = 0

        def lookup(session, request):
            nonlocal calls
            calls += 1
            if calls == 2:
                # Commit after the last lookup but before our insert.
                self.insert_competing_request(competing_key)
                return None
            return original(session, request)

        return lookup

    def test_insert_collision_returns_identical_committed_request(self):
        with patch.object(
            service, "find_existing",
            side_effect=self.collision_lookup(self.request.request_key),
        ):
            result = service.submit_factory_request(self.request)
        self.assertEqual(result["request_key"], self.request.request_key)
        self.assertEqual(result["status"], "awaiting_review")
        self.assertEqual(len(self.rows()), 1)

    def test_insert_collision_rejects_competing_candidate_request(self):
        with patch.object(
            service, "find_existing",
            side_effect=self.collision_lookup("agent:competing-request"),
        ):
            with self.assertRaisesRegex(ValueError, "already has"):
                service.submit_factory_request(self.request)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["request_key"], "agent:competing-request")


if __name__ == "__main__":
    unittest.main()
