"""Opt-in PostgreSQL tests using disposable, isolated schemas."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.market_db.database import engine as application_engine
from app.market_db.models import Portfolio
from app.market_db.migrate_experiment_factory import migrate_experiment_factory
from app.capital import experiment_factory_service as service
from app.capital import experiment_factory_creation as creation
from app.capital.experiment_factory_models import ExperimentFactoryRequest
from app.capital.experiment_factory_packet import packet_digest
from app.capital.experiment_factory_store import ExperimentFactoryRecord


@unittest.skipUnless(
    os.getenv("FACTORY_POSTGRES_TEST") == "1",
    "Set FACTORY_POSTGRES_TEST=1 to run isolated PostgreSQL tests.",
)
class FactoryPostgresTests(unittest.TestCase):
    def setUp(self):
        if application_engine.dialect.name != "postgresql":
            self.fail("These tests require PostgreSQL.")

        self.schema = "factory_test_" + uuid4().hex
        with application_engine.begin() as connection:
            connection.execute(CreateSchema(self.schema))
        self.addCleanup(self.drop_schema)

        self.engine = create_engine(
            application_engine.url,
            connect_args={
                "options": (
                    f"-csearch_path={self.schema} "
                    "-cstatement_timeout=15000 -clock_timeout=10000"
                )
            },
        )
        self.addCleanup(self.engine.dispose)
        Portfolio.__table__.create(self.engine)
        migrate_experiment_factory(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.patch(service, "SessionLocal", self.sessions)
        self.patch(creation, "SessionLocal", self.sessions)
        self.request = ExperimentFactoryRequest(
            request_key="agent:request-1",
            research_id="research_test",
            requested_by="test-agent",
        )

    def patch(self, target, name, value):
        handle = patch.object(target, name, value)
        handle.start()
        self.addCleanup(handle.stop)

    def drop_schema(self):
        expected = "factory_test_"
        suffix = self.schema.removeprefix(expected)
        if (
            not self.schema.startswith(expected)
            or len(suffix) != 32
            or any(character not in "0123456789abcdef" for character in suffix)
        ):
            raise RuntimeError("Unexpected temporary schema name.")
        with application_engine.begin() as connection:
            connection.execute(DropSchema(self.schema, cascade=True))

    def seed_request(self):
        with self.sessions.begin() as session:
            session.add(ExperimentFactoryRecord(
                **self.request.model_dump(), status="awaiting_review"
            ))

    def prepare_creation(self):
        payload = {
            "schema_version": 1,
            "action": "create_inactive_paper_experiment",
            "request": self.request.model_dump(),
            "review": {
                "research": {
                    "research_id": "research_test",
                    "strategy_name": "mean_reversion_v2",
                    "hypothesis_version": 2,
                },
                "strategy": {"version": "2.0"},
                "proposed_experiment": {
                    "status": "planned",
                    "execution_mode": "disabled",
                    "portfolio_type": "paper",
                    "portfolio_active": False,
                    "duration_days": 180,
                    "starting_capital_usd": "1000.00",
                    "policy": {"name": "mean_reversion_v2_1000"},
                },
            },
        }
        packet = {"payload": payload, "sha256": packet_digest(payload)}
        self.patch(creation, "prepare_factory_packet", lambda key: packet)
        return {
            "request_key": self.request.request_key,
            "packet_sha256": packet["sha256"],
            "operator": creation.pwd.getpwuid(os.getuid()).pw_name,
            "operator_uid": os.getuid(),
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
            "action": payload["action"],
        }

    def test_migration_is_isolated(self):
        with self.engine.connect() as connection:
            from sqlalchemy import text
            self.assertEqual(
                connection.execute(text("SELECT current_schema()")).scalar_one(),
                self.schema,
            )
        self.assertEqual(
            set(inspect(self.engine).get_table_names()),
            {"portfolios", "capital_experiment_factory"},
        )

    def test_concurrent_submission_creates_one_request(self):
        barrier = Barrier(2)

        def candidate(**kwargs):
            barrier.wait(timeout=10)
            return SimpleNamespace(status=SimpleNamespace(value="proposed"))

        self.patch(service, "require_research_candidate", candidate)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(service.submit_factory_request, self.request)
                for _ in range(2)
            ]
            results = [future.result(timeout=25) for future in futures]

        self.assertEqual(results[0], results[1])
        with self.sessions() as session:
            self.assertEqual(
                len(session.scalars(select(ExperimentFactoryRecord)).all()), 1
            )
            self.assertEqual(session.scalars(select(Portfolio)).all(), [])

    def test_concurrent_creation_creates_one_inactive_portfolio(self):
        self.seed_request()
        confirmation = self.prepare_creation()
        barrier = Barrier(2)

        def create():
            barrier.wait(timeout=10)
            return creation.create_confirmed_experiment(confirmation)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create) for _ in range(2)]
            results = [future.result(timeout=25) for future in futures]

        self.assertEqual(results[0], results[1])
        with self.sessions() as session:
            portfolios = session.scalars(select(Portfolio)).all()
            self.assertEqual(len(portfolios), 1)
            self.assertFalse(portfolios[0].is_active)
            self.assertEqual(portfolios[0].portfolio_type, "paper")
            record = session.get(
                ExperimentFactoryRecord, self.request.request_key
            )
            self.assertEqual(record.status, "created")
            self.assertEqual(record.portfolio_id, portfolios[0].id)

    def test_record_failure_rolls_back_portfolio(self):
        self.seed_request()
        confirmation = self.prepare_creation()
        original = Session.flush

        def fail_record_flush(session, *args, **kwargs):
            if any(
                isinstance(row, ExperimentFactoryRecord)
                and row.status == "created"
                for row in session.dirty
            ):
                raise RuntimeError("Synthetic record failure")
            return original(session, *args, **kwargs)

        with patch.object(Session, "flush", fail_record_flush):
            with self.assertRaisesRegex(RuntimeError, "record failure"):
                creation.create_confirmed_experiment(confirmation)

        with self.sessions() as session:
            self.assertEqual(session.scalars(select(Portfolio)).all(), [])
            record = session.get(
                ExperimentFactoryRecord, self.request.request_key
            )
            self.assertEqual(record.status, "awaiting_review")
            self.assertIsNone(record.portfolio_id)


if __name__ == "__main__":
    unittest.main()
