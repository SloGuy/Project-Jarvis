"""Opt-in PostgreSQL tests using a disposable lifecycle schema."""

import os
import unittest
from uuid import uuid4

from sqlalchemy import inspect, text
from sqlalchemy.exc import DataError, IntegrityError

from app.market_db.database import engine
from app.market_db.migrate_paper_lifecycle import migrate_paper_lifecycle


@unittest.skipUnless(
    os.environ.get("PAPER_LIFECYCLE_POSTGRES_TEST") == "1",
    "Set PAPER_LIFECYCLE_POSTGRES_TEST=1 to run isolated database tests.",
)
class PaperLifecycleMigrationTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(engine.dialect.name, "postgresql")
        self.schema = "test_paper_lifecycle_" + uuid4().hex
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.addCleanup(self.cleanup_schema)
        with engine.begin() as connection:
            connection.execute(text(
                f'CREATE TABLE "{self.schema}".portfolios '
                "(id INTEGER PRIMARY KEY)"
            ))
            connection.execute(text(
                f'CREATE TABLE "{self.schema}".capital_experiment_factory '
                "(request_key VARCHAR(100) PRIMARY KEY)"
            ))
            connection.execute(text(
                f'INSERT INTO "{self.schema}".portfolios VALUES (1), (2)'
            ))
            connection.execute(text(
                f'INSERT INTO "{self.schema}".capital_experiment_factory '
                "VALUES ('request-1'), ('request-2')"
            ))

    def cleanup_schema(self):
        # Only the randomly named schema created by this test is removed.
        with engine.begin() as connection:
            connection.execute(text(
                f'DROP SCHEMA "{self.schema}" CASCADE'
            ))

    def install(self):
        return migrate_paper_lifecycle(
            schema=self.schema, database_engine=engine
        )

    def insert(self, **overrides):
        values = {
            "request": "request-1",
            "portfolio": 1,
            "status": "planned",
            "mode": "paper",
            "allocation": "0",
            "version": 1,
        }
        values.update(overrides)
        with engine.begin() as connection:
            connection.execute(text(
                f'INSERT INTO "{self.schema}".capital_paper_lifecycle '
                "(request_key, portfolio_id, status, execution_mode, "
                "allocation_usd, policy_version, authorization_snapshot, "
                "transition_history, version, created_at, updated_at) "
                "VALUES (:request, :portfolio, :status, :mode, :allocation, "
                "'test-policy', '{}', '[]', :version, now(), now())"
            ), values)

    def test_installation_creates_only_lifecycle_table(self):
        result = self.install()
        self.assertEqual(result["status"], "installed")
        self.assertFalse(result["portfolios_modified"])
        self.assertFalse(result["experiments_activated"])
        self.assertEqual(
            set(inspect(engine).get_table_names(schema=self.schema)),
            {
                "portfolios",
                "capital_experiment_factory",
                "capital_paper_lifecycle",
            },
        )
        with engine.connect() as connection:
            count = connection.scalar(text(
                f'SELECT count(*) FROM "{self.schema}".portfolios'
            ))
        self.assertEqual(count, 2)

    def test_valid_planned_and_active_records(self):
        self.install()
        self.insert()
        self.insert(
            request="request-2", portfolio=2,
            status="active", allocation="1000",
        )

    def test_invalid_states_and_allocations_are_rejected(self):
        self.install()
        cases = [
            {"status": "live"},
            {"mode": "live"},
            {"allocation": "-1"},
            {"allocation": "NaN"},
            {"allocation": "1000000000000"},
            {"allocation": "1"},
            {"status": "paused", "allocation": "1"},
            {"status": "demoted", "allocation": "1"},
            {"status": "retired", "allocation": "1"},
            {"version": 0},
        ]
        for case in cases:
            with self.subTest(case=case):
                expected = (
                    DataError
                    if case.get("allocation") == "1000000000000"
                    else IntegrityError
                )
                with self.assertRaises(expected):
                    self.insert(**case)

    def test_foreign_keys_and_unique_portfolio(self):
        self.install()
        with self.assertRaises(IntegrityError):
            self.insert(request="missing")
        with self.assertRaises(IntegrityError):
            self.insert(portfolio=999)
        self.insert()
        with self.assertRaises(IntegrityError):
            self.insert(request="request-2", portfolio=1)

    def test_repeat_installation_preserves_existing_record(self):
        self.install()
        self.insert()
        with self.assertRaises(ValueError):
            self.install()
        with engine.connect() as connection:
            count = connection.scalar(text(
                f'SELECT count(*) FROM '
                f'"{self.schema}".capital_paper_lifecycle'
            ))
        self.assertEqual(count, 1)

    def test_missing_prerequisite_creates_nothing(self):
        with engine.begin() as connection:
            connection.execute(text(
                f'DROP TABLE "{self.schema}".capital_experiment_factory'
            ))
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(inspect(engine).has_table(
            "capital_paper_lifecycle", schema=self.schema
        ))

    def test_post_creation_failure_rolls_back_table(self):
        from unittest.mock import patch
        from sqlalchemy.engine.reflection import Inspector

        with patch.object(Inspector, "get_foreign_keys", return_value=[]):
            with self.assertRaises(RuntimeError):
                self.install()
        self.assertFalse(inspect(engine).has_table(
            "capital_paper_lifecycle", schema=self.schema
        ))


if __name__ == "__main__":
    unittest.main()
