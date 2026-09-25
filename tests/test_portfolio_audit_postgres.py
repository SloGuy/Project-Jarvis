"""Opt-in accounting audit tests in disposable PostgreSQL schemas.

Uses minimal accounting tables with representative numeric and FK fields.
No application accounting tables are modified.
"""

import json
import os
from decimal import Decimal
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateSchema, DropSchema

from app.market_db.database import engine as application_engine
from app.market_db import migrate_portfolio_audit as migration


@unittest.skipUnless(
    os.getenv("PORTFOLIO_AUDIT_POSTGRES_TEST") == "1",
    "Set PORTFOLIO_AUDIT_POSTGRES_TEST=1 for isolated PostgreSQL tests.",
)
class PortfolioAuditPostgresTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(application_engine.dialect.name, "postgresql")
        self.assertEqual(application_engine.dialect.driver, "psycopg")

        self.schema = "portfolio_audit_test_" + uuid4().hex
        with application_engine.begin() as connection:
            connection.execute(CreateSchema(self.schema))
        self.addCleanup(self.drop_schema)

        self.engine = create_engine(
            application_engine.url,
            connect_args={
                "options": (
                    f"-csearch_path={self.schema} "
                    "-cstatement_timeout=15000 -clock_timeout=10000"
                ),
            },
        )
        self.addCleanup(self.engine.dispose)

        statements = [
            """
            CREATE TABLE portfolios (
                id BIGINT PRIMARY KEY,
                name TEXT NOT NULL,
                portfolio_type TEXT NOT NULL,
                cash_balance_usd NUMERIC(20,8) NOT NULL,
                is_active BOOLEAN NOT NULL
            )
            """,
            """
            CREATE TABLE portfolio_positions (
                id BIGINT PRIMARY KEY,
                portfolio_id BIGINT NOT NULL
                    REFERENCES portfolios(id) ON DELETE CASCADE,
                asset_id BIGINT NOT NULL,
                quantity NUMERIC(28,12) NOT NULL,
                average_cost_usd NUMERIC(20,8) NOT NULL
            )
            """,
            """
            CREATE TABLE portfolio_transactions (
                id BIGINT PRIMARY KEY,
                portfolio_id BIGINT NOT NULL
                    REFERENCES portfolios(id) ON DELETE CASCADE,
                asset_id BIGINT,
                transaction_type TEXT NOT NULL,
                quantity NUMERIC(28,12) NOT NULL,
                price_usd NUMERIC(20,8) NOT NULL,
                total_usd NUMERIC(20,8) NOT NULL,
                fees_usd NUMERIC(20,8) NOT NULL
            )
            """,
            """
            INSERT INTO portfolios
            VALUES (1, 'Test Paper', 'paper', 899.00000001, TRUE)
            """,
            """
            INSERT INTO portfolio_positions
            VALUES (10, 1, 7, 2.000000000001, 50)
            """,
            """
            INSERT INTO portfolio_transactions
            VALUES (20, 1, 7, 'buy', 2.000000000001, 50, 100, 1)
            """,
        ]
        with self.engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))

    def drop_schema(self):
        prefix = "portfolio_audit_test_"
        suffix = self.schema.removeprefix(prefix)
        if (
            not self.schema.startswith(prefix)
            or len(suffix) != 32
            or any(character not in "0123456789abcdef" for character in suffix)
        ):
            raise RuntimeError("Unexpected test schema name.")
        with application_engine.begin() as connection:
            connection.execute(DropSchema(self.schema, cascade=True))

    def install(self):
        return migration.migrate_portfolio_audit(
            schema=self.schema,
            database_engine=self.engine,
        )

    def scalar(self, statement):
        with self.engine.connect() as connection:
            return connection.scalar(text(statement))

    def events(self, operation=None):
        with self.engine.connect() as connection:
            statement = "SELECT * FROM capital_accounting_audit"
            parameters = {}
            if operation:
                statement += " WHERE operation = :operation"
                parameters["operation"] = operation
            statement += " ORDER BY event_id"
            return connection.execute(
                text(statement), parameters
            ).mappings().all()

    def test_baseline_captures_existing_rows_without_changing_them(self):
        result = self.install()
        self.assertEqual(result["baseline_counts"], {
            "portfolios": 1,
            "portfolio_positions": 1,
            "portfolio_transactions": 1,
        })
        self.assertEqual(len(self.events("BASELINE")), 3)
        self.assertEqual(
            self.scalar("SELECT cash_balance_usd FROM portfolios WHERE id = 1"),
            Decimal("899.00000001"),
        )
        self.assertEqual(self.scalar("SELECT current_schema()"), self.schema)
        self.assertEqual(
            set(inspect(self.engine).get_table_names()),
            {
                "portfolios",
                "portfolio_positions",
                "portfolio_transactions",
                "capital_accounting_audit",
                "capital_accounting_audit_installation",
            },
        )

    def test_baseline_json_preserves_numeric_precision(self):
        self.install()
        event = next(
            row for row in self.events("BASELINE")
            if row["source_table"] == "portfolio_positions"
        )
        image = json.loads(event["new_row_json"], parse_float=Decimal)
        self.assertEqual(image["quantity"], Decimal("2.000000000001"))

    def test_update_preserves_before_and_after_images(self):
        self.install()
        with self.engine.begin() as connection:
            connection.execute(text(
                "UPDATE portfolios SET cash_balance_usd = 900 WHERE id = 1"
            ))
        event = self.events("UPDATE")[0]
        before = json.loads(event["old_row_json"], parse_float=Decimal)
        after = json.loads(event["new_row_json"], parse_float=Decimal)
        self.assertEqual(before["cash_balance_usd"], Decimal("899.00000001"))
        self.assertEqual(after["cash_balance_usd"], Decimal("900"))
        self.assertEqual(event["old_portfolio_id"], 1)
        self.assertEqual(event["new_portfolio_id"], 1)
        self.assertEqual(event["source_schema"], self.schema)

    def test_new_portfolio_insert_is_recorded(self):
        self.install()
        with self.engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO portfolios VALUES "
                "(2, 'Another Paper', 'paper', 1000, FALSE)"
            ))
        event = self.events("INSERT")[0]
        self.assertEqual(event["source_row_id"], 2)
        self.assertIsNone(event["old_row_json"])
        self.assertEqual(event["new_portfolio_id"], 2)

    def test_reset_style_deletions_preserve_old_rows(self):
        self.install()
        with self.engine.begin() as connection:
            connection.execute(text(
                "DELETE FROM portfolio_transactions WHERE portfolio_id = 1"
            ))
            connection.execute(text(
                "DELETE FROM portfolio_positions WHERE portfolio_id = 1"
            ))
            connection.execute(text(
                "UPDATE portfolios SET cash_balance_usd = 1000 WHERE id = 1"
            ))

        changed = [
            row for row in self.events() if row["operation"] != "BASELINE"
        ]
        self.assertEqual(len(changed), 3)
        self.assertEqual(len({row["database_transaction_id"] for row in changed}), 1)
        self.assertEqual(len(self.events("DELETE")), 2)
        for event in self.events("DELETE"):
            self.assertIsNotNone(event["old_row_json"])
            self.assertIsNone(event["new_row_json"])
        self.assertEqual(self.scalar("SELECT count(*) FROM portfolio_positions"), 0)

    def test_cascade_deletion_preserves_all_evidence(self):
        self.install()
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM portfolios WHERE id = 1"))
        self.assertEqual(len(self.events("DELETE")), 3)
        self.assertEqual(len(self.events("BASELINE")), 3)
        self.assertEqual(self.scalar("SELECT count(*) FROM portfolios"), 0)

    def test_transaction_rollback_removes_audit_and_accounting_changes(self):
        self.install()
        with self.assertRaisesRegex(RuntimeError, "force rollback"):
            with self.engine.begin() as connection:
                connection.execute(text(
                    "UPDATE portfolios SET cash_balance_usd = 42 WHERE id = 1"
                ))
                raise RuntimeError("force rollback")
        self.assertEqual(self.events("UPDATE"), [])
        self.assertEqual(
            self.scalar("SELECT cash_balance_usd FROM portfolios WHERE id = 1"),
            Decimal("899.00000001"),
        )

    def test_audit_write_failure_rolls_back_accounting_change(self):
        self.install()
        with self.engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE capital_accounting_audit "
                "ADD CONSTRAINT test_reject_new_events "
                "CHECK (operation = 'BASELINE')"
            ))
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as connection:
                connection.execute(text(
                    "UPDATE portfolios SET cash_balance_usd = 42 WHERE id = 1"
                ))
        self.assertEqual(
            self.scalar("SELECT cash_balance_usd FROM portfolios WHERE id = 1"),
            Decimal("899.00000001"),
        )
        self.assertEqual(self.events("UPDATE"), [])

    def test_truncate_is_blocked_for_all_sources(self):
        self.install()
        for table in migration.SOURCE_TABLES:
            with self.subTest(table=table):
                with self.assertRaises(DBAPIError) as raised:
                    with self.engine.begin() as connection:
                        connection.execute(text(f"TRUNCATE {table} CASCADE"))
                self.assertIn("accounting audit safeguards", str(raised.exception))
        self.assertEqual(self.scalar("SELECT count(*) FROM portfolios"), 1)

    def test_audit_and_installation_modification_are_blocked(self):
        self.install()
        for statement in (
            "DELETE FROM capital_accounting_audit",
            "UPDATE capital_accounting_audit SET source_row_id = 999",
            "TRUNCATE capital_accounting_audit",
            "DELETE FROM capital_accounting_audit_installation",
            "UPDATE capital_accounting_audit_installation SET schema_version = 1",
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(DBAPIError):
                    with self.engine.begin() as connection:
                        connection.execute(text(statement))
        self.assertEqual(len(self.events()), 3)

    def test_repeat_installation_is_rejected_without_modification(self):
        first = self.install()
        with self.assertRaisesRegex(RuntimeError, "already exist"):
            self.install()
        self.assertEqual(len(self.events()), 3)
        self.assertEqual(
            str(self.scalar(
                "SELECT installation_id FROM capital_accounting_audit_installation"
            )),
            first["installation_id"],
        )

    def test_failed_trigger_installation_rolls_back_entire_migration(self):
        original = migration._execute_sql_file

        def execute(connection, filename):
            if filename == "portfolio_audit_triggers.sql":
                raise RuntimeError("synthetic trigger failure")
            return original(connection, filename)

        with patch.object(migration, "_execute_sql_file", side_effect=execute):
            with self.assertRaisesRegex(RuntimeError, "synthetic trigger failure"):
                self.install()

        self.assertEqual(
            set(inspect(self.engine).get_table_names()),
            set(migration.SOURCE_TABLES),
        )
        self.assertEqual(self.scalar("SELECT count(*) FROM portfolios"), 1)
        # A clean retry must work after transactional DDL rollback.
        self.assertEqual(self.install()["status"], "installed")


if __name__ == "__main__":
    unittest.main()
