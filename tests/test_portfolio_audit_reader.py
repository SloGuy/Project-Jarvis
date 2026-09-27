"""Audit reader tests using disposable PostgreSQL schemas."""
import json
import os
from decimal import Decimal
import unittest
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import test_portfolio_audit_postgres as fixtures
from app.capital import portfolio_audit_reader as reader


@unittest.skipUnless(
    os.getenv("PORTFOLIO_AUDIT_POSTGRES_TEST") == "1",
    "Requires opt-in isolated PostgreSQL tests.",
)
class PortfolioAuditReaderTests(unittest.TestCase):
    setUp = fixtures.PortfolioAuditPostgresTests.setUp
    drop_schema = fixtures.PortfolioAuditPostgresTests.drop_schema
    install = fixtures.PortfolioAuditPostgresTests.install
    scalar = fixtures.PortfolioAuditPostgresTests.scalar

    def read(self, **kwargs):
        return reader.read_portfolio_audit(
            schema=self.schema,
            database_engine=self.engine,
            **kwargs,
        )

    def test_baseline_and_current_rows_preserve_precision(self):
        installed = self.install()
        result = self.read()
        self.assertEqual(
            result["installation"]["installation_id"],
            installed["installation_id"],
        )
        self.assertEqual(result["event_count"], 3)
        position = next(
            event for event in result["events"]
            if event["source_table"] == "portfolio_positions"
        )
        self.assertEqual(
            position["after"]["quantity"],
            Decimal("2.000000000001"),
        )
        raw = result["current_rows"]["portfolio_positions"][0]["row_json"]
        self.assertIsInstance(raw, str)
        self.assertEqual(
            json.loads(raw, parse_float=Decimal)["quantity"],
            Decimal("2.000000000001"),
        )

    def test_committed_update_is_visible(self):
        self.install()
        with self.engine.begin() as connection:
            connection.execute(text(
                "UPDATE portfolios SET cash_balance_usd = 900 WHERE id = 1"
            ))
        result = self.read()
        updates = [
            event for event in result["events"]
            if event["operation"] == "UPDATE"
        ]
        self.assertEqual(len(updates), 1)
        self.assertEqual(
            updates[0]["before"]["cash_balance_usd"],
            Decimal("899.00000001"),
        )
        self.assertEqual(
            updates[0]["after"]["cash_balance_usd"],
            Decimal("900"),
        )

    def test_snapshot_excludes_concurrent_commit_from_both_views(self):
        self.install()
        original = reader._bounded_rows
        committed = False

        def read_then_commit(connection, statement, maximum):
            nonlocal committed
            rows = original(connection, statement, maximum)
            if not committed:
                committed = True
                with self.engine.begin() as writer:
                    writer.execute(text(
                        "UPDATE portfolios SET cash_balance_usd = 901 "
                        "WHERE id = 1"
                    ))
            return rows

        with patch.object(reader, "_bounded_rows", side_effect=read_then_commit):
            result = self.read()

        self.assertTrue(committed)
        self.assertEqual(result["event_count"], 3)
        current = json.loads(
            result["current_rows"]["portfolios"][0]["row_json"],
            parse_float=Decimal,
        )
        self.assertEqual(current["cash_balance_usd"], Decimal("899.00000001"))
        self.assertEqual(self.read()["event_count"], 4)
        self.assertEqual(
            self.scalar("SELECT cash_balance_usd FROM portfolios WHERE id = 1"),
            Decimal("901"),
        )

    def test_transaction_is_actually_read_only(self):
        self.install()
        original = reader._bounded_rows
        checked = False

        def attempt_write(connection, statement, maximum):
            nonlocal checked
            if not checked:
                checked = True
                self.assertEqual(
                    connection.scalar(text("SHOW transaction_read_only")),
                    "on",
                )
                self.assertEqual(
                    connection.scalar(text("SHOW transaction_isolation")),
                    "repeatable read",
                )
                with self.assertRaises(DBAPIError):
                    with connection.begin_nested():
                        connection.execute(text(
                            "UPDATE portfolios SET cash_balance_usd = 0 "
                            "WHERE id = 1"
                        ))
            return original(connection, statement, maximum)

        with patch.object(reader, "_bounded_rows", side_effect=attempt_write):
            self.read()
        self.assertTrue(checked)
        self.assertEqual(
            self.scalar("SELECT cash_balance_usd FROM portfolios WHERE id = 1"),
            Decimal("899.00000001"),
        )
        self.assertEqual(
            self.scalar("SELECT count(*) FROM capital_accounting_audit"),
            3,
        )

    def test_event_limit_refuses_partial_evidence(self):
        self.install()
        with self.assertRaisesRegex(ValueError, "row limit exceeded"):
            self.read(maximum_rows=2)
        self.assertEqual(self.read(maximum_rows=3)["event_count"], 3)

    def test_source_limit_also_refuses_partial_evidence(self):
        self.install()
        # Deliberately simulate a coverage gap in this disposable schema.
        with self.engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE portfolios DISABLE TRIGGER USER"
            ))
            connection.execute(text(
                "INSERT INTO portfolios "
                "SELECT id, 'Extra', 'paper', 100, TRUE "
                "FROM generate_series(2, 5) AS id"
            ))
            connection.execute(text(
                "ALTER TABLE portfolios ENABLE TRIGGER USER"
            ))
        with self.assertRaisesRegex(ValueError, "row limit exceeded"):
            self.read(maximum_rows=3)

    def test_missing_installation_is_not_created(self):
        with self.assertRaises(DBAPIError):
            self.read()
        self.assertIsNone(self.scalar(
            "SELECT to_regclass('capital_accounting_audit')"
        ))

    def test_deleted_baseline_event_is_rejected(self):
        self.install()
        with self.engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE capital_accounting_audit DISABLE TRIGGER USER"
            ))
            connection.execute(text(
                "DELETE FROM capital_accounting_audit "
                "WHERE source_table = 'portfolio_positions'"
            ))
            connection.execute(text(
                "ALTER TABLE capital_accounting_audit ENABLE TRIGGER USER"
            ))
        with self.assertRaisesRegex(ValueError, "Baseline event counts"):
            self.read()

    def test_wrong_event_binding_is_rejected(self):
        self.install()
        with self.engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE capital_accounting_audit DISABLE TRIGGER USER"
            ))
            connection.execute(text(
                "UPDATE capital_accounting_audit SET source_schema = 'wrong'"
            ))
            connection.execute(text(
                "ALTER TABLE capital_accounting_audit ENABLE TRIGGER USER"
            ))
        with self.assertRaisesRegex(ValueError, "schema mismatch"):
            self.read()

    def test_invalid_limits_are_rejected(self):
        for value in (True, 0, -1, 1.5, "10", 1000001):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.read(maximum_rows=value)

    def test_invalid_schema_is_rejected(self):
        with self.assertRaises(ValueError):
            reader.read_portfolio_audit(
                schema="public; SELECT 1",
                database_engine=self.engine,
            )

    def test_reader_does_not_claim_continuity_or_authority(self):
        self.install()
        result = self.read()
        self.assertTrue(result["database_read_only"])
        self.assertTrue(result["visibility_snapshot"])
        for name in (
            "database_writes",
            "continuity_verified",
            "commit_order_verified",
            "function_bodies_verified",
            "historical_completeness_verified",
            "execution_authorized",
        ):
            with self.subTest(name=name):
                self.assertIs(result[name], False)


if __name__ == "__main__":
    unittest.main()
