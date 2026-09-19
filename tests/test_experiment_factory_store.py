"""Factory schema tests using an isolated SQLite database."""
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError

from app.capital.experiment_factory_store import ExperimentFactoryRecord
from app.market_db.models import Portfolio


class FactoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys = ON")

        Portfolio.__table__.create(self.engine)
        ExperimentFactoryRecord.__table__.create(self.engine)
        self.table = ExperimentFactoryRecord.__table__

    def insert_request(self, **overrides):
        values = {
            "request_key": "agent:request-1",
            "research_id": "research_test",
            "requested_by": "research-agent",
            "status": "awaiting_review",
        }
        values.update(overrides)
        with self.engine.begin() as connection:
            connection.execute(self.table.insert().values(**values))

    def approval_fields(self, portfolio_id):
        return {
            "status": "created",
            "portfolio_id": portfolio_id,
            "approved_by": "test-operator",
            "approved_at": datetime.now(timezone.utc),
            "approval_snapshot": {"test_fixture": True},
            "approval_sha256": "a" * 64,
        }

    def test_submission_stores_no_approval_or_portfolio(self):
        self.insert_request()
        with self.engine.connect() as connection:
            row = connection.execute(self.table.select()).mappings().one()
        self.assertEqual(row["status"], "awaiting_review")
        for field in (
            "portfolio_id", "approved_by", "approved_at",
            "approval_snapshot", "approval_sha256",
        ):
            self.assertIsNone(row[field], field)

    def test_request_key_is_unique(self):
        self.insert_request()
        with self.assertRaises(IntegrityError):
            self.insert_request(research_id="research_other")

    def test_candidate_is_unique(self):
        self.insert_request()
        with self.assertRaises(IntegrityError):
            self.insert_request(request_key="agent:request-2")

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(IntegrityError):
            self.insert_request(status="live")

    def test_created_requires_every_approval_field(self):
        complete = self.approval_fields(self.create_portfolio())
        for field in (
            "portfolio_id", "approved_by", "approved_at",
            "approval_snapshot", "approval_sha256",
        ):
            with self.subTest(field=field):
                values = {**complete, field: None}
                with self.assertRaises(IntegrityError):
                    self.insert_request(**values)

    def test_unapproved_states_cannot_store_approval(self):
        for status in ("awaiting_review", "rejected"):
            with self.subTest(status=status):
                with self.assertRaises(IntegrityError):
                    self.insert_request(
                        status=status,
                        approved_by="claimed-operator",
                    )

    def test_portfolio_must_exist(self):
        with self.assertRaises(IntegrityError):
            self.insert_request(**self.approval_fields(999))

    def create_portfolio(self):
        with self.engine.begin() as connection:
            result = connection.execute(Portfolio.__table__.insert().values(
                name="Factory test only",
                portfolio_type="paper",
                cash_balance_usd=Decimal("1000"),
                is_active=False,
            ))
            return result.inserted_primary_key[0]

    def test_created_record_and_unique_portfolio_binding(self):
        portfolio_id = self.create_portfolio()
        approval = self.approval_fields(portfolio_id)
        self.insert_request(**approval)
        with self.assertRaises(IntegrityError):
            self.insert_request(
                request_key="agent:request-2",
                research_id="research_other",
                **approval,
            )


if __name__ == "__main__":
    unittest.main()
