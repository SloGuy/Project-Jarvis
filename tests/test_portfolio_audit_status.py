"""Opt-in audit status tests using disposable PostgreSQL schemas."""

import unittest

from sqlalchemy import text

from app.capital.portfolio_audit_status import get_portfolio_audit_status
from test_portfolio_audit_postgres import PortfolioAuditPostgresTests


class PortfolioAuditStatusTests(unittest.TestCase):
    setUp = PortfolioAuditPostgresTests.setUp
    drop_schema = PortfolioAuditPostgresTests.drop_schema
    install = PortfolioAuditPostgresTests.install
    scalar = PortfolioAuditPostgresTests.scalar
    events = PortfolioAuditPostgresTests.events

    __unittest_skip__ = getattr(
        PortfolioAuditPostgresTests, "__unittest_skip__", False
    )
    __unittest_skip_why__ = getattr(
        PortfolioAuditPostgresTests, "__unittest_skip_why__", ""
    )

    def status(self):
        return get_portfolio_audit_status(
            schema=self.schema,
            database_engine=self.engine,
        )

    def execute(self, statement):
        with self.engine.begin() as connection:
            connection.execute(text(statement))

    def test_absent_installation_is_reported_without_creating_objects(self):
        result = self.status()
        self.assertEqual(result["status"], "not_installed")
        self.assertEqual(result["issues"], [])
        self.assertIsNone(result["installation_id"])
        self.assertIsNone(self.scalar(
            "SELECT to_regclass('capital_accounting_audit')"
        ))

    def test_valid_installation_passes_structural_checks(self):
        installed = self.install()
        result = self.status()
        self.assertEqual(result["status"], "structural_checks_passed")
        self.assertEqual(result["issues"], [])
        self.assertEqual(
            result["installation_id"], installed["installation_id"]
        )
        for counts in result["baseline_counts"].values():
            self.assertEqual(counts, {"recorded": 1, "expected": 1})

    def test_missing_schema_is_reported(self):
        result = get_portfolio_audit_status(
            schema=self.schema + "_missing",
            database_engine=self.engine,
        )
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("schema_missing", result["issues"])

    def test_partial_installation_is_not_reported_as_absent(self):
        self.execute("""
            CREATE TABLE capital_accounting_audit_installation (
                placeholder INTEGER
            )
        """)
        result = self.status()
        self.assertEqual(result["status"], "incomplete")
        self.assertIn(
            "required_table_missing:capital_accounting_audit",
            result["issues"],
        )

    def test_disabled_source_trigger_is_detected(self):
        self.install()
        self.execute("""
            ALTER TABLE portfolios
            DISABLE TRIGGER capital_accounting_audit_portfolios
        """)
        result = self.status()
        self.assertEqual(result["status"], "incomplete")
        self.assertIn(
            "trigger_mismatch:portfolios:capital_accounting_audit_portfolios",
            result["issues"],
        )

    def test_missing_truncate_guard_is_detected(self):
        self.install()
        self.execute("""
            DROP TRIGGER capital_accounting_audit_no_truncate
            ON portfolio_positions
        """)
        result = self.status()
        self.assertIn(
            "trigger_missing:portfolio_positions:capital_accounting_audit_no_truncate",
            result["issues"],
        )

    def test_replica_only_trigger_is_not_accepted(self):
        self.install()
        self.execute("""
            ALTER TABLE portfolios
            ENABLE REPLICA TRIGGER capital_accounting_audit_portfolios
        """)
        result = self.status()
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any(
            issue.startswith("trigger_mismatch:portfolios:")
            for issue in result["issues"]
        ))

    def test_deleted_baseline_event_is_detected(self):
        self.install()
        # Simulate owner-level damage inside the disposable schema only.
        self.execute("""
            ALTER TABLE capital_accounting_audit
            DISABLE TRIGGER capital_accounting_audit_preserve_records
        """)
        self.execute("""
            DELETE FROM capital_accounting_audit
            WHERE operation = 'BASELINE' AND source_table = 'portfolios'
        """)
        self.execute("""
            ALTER TABLE capital_accounting_audit
            ENABLE TRIGGER capital_accounting_audit_preserve_records
        """)
        result = self.status()
        self.assertIn(
            "baseline_count_mismatch:portfolios", result["issues"]
        )

    def test_wrong_source_schema_binding_is_detected(self):
        self.install()
        self.execute("""
            ALTER TABLE capital_accounting_audit
            DISABLE TRIGGER capital_accounting_audit_preserve_records
        """)
        self.execute("""
            UPDATE capital_accounting_audit
            SET source_schema = 'incorrect'
            WHERE source_table = 'portfolios'
        """)
        self.execute("""
            ALTER TABLE capital_accounting_audit
            ENABLE TRIGGER capital_accounting_audit_preserve_records
        """)
        result = self.status()
        self.assertIn(
            "event_installation_binding_mismatch", result["issues"]
        )

    def test_changed_baseline_transaction_binding_is_detected(self):
        self.install()
        self.execute("""
            ALTER TABLE capital_accounting_audit
            DISABLE TRIGGER capital_accounting_audit_preserve_records
        """)
        self.execute("""
            UPDATE capital_accounting_audit
            SET database_transaction_id = database_transaction_id + 1
            WHERE source_table = 'portfolios'
        """)
        self.execute("""
            ALTER TABLE capital_accounting_audit
            ENABLE TRIGGER capital_accounting_audit_preserve_records
        """)
        result = self.status()
        self.assertIn(
            "event_installation_binding_mismatch", result["issues"]
        )

    def test_incomplete_baseline_metadata_is_detected(self):
        self.install()
        self.execute("""
            ALTER TABLE capital_accounting_audit_installation
            DISABLE TRIGGER capital_accounting_audit_preserve_installation
        """)
        self.execute("""
            UPDATE capital_accounting_audit_installation
            SET baseline_finished_at = NULL
        """)
        self.execute("""
            ALTER TABLE capital_accounting_audit_installation
            ENABLE TRIGGER capital_accounting_audit_preserve_installation
        """)
        result = self.status()
        self.assertIn("invalid_installation_metadata", result["issues"])

    def test_normal_accounting_change_does_not_break_baseline_counts(self):
        self.install()
        self.execute("""
            UPDATE portfolios SET cash_balance_usd = 950 WHERE id = 1
        """)
        result = self.status()
        self.assertEqual(result["status"], "structural_checks_passed")
        self.assertEqual(
            result["baseline_counts"]["portfolios"],
            {"recorded": 1, "expected": 1},
        )

    def test_status_read_does_not_add_events_or_change_cash(self):
        self.install()
        before_events = [dict(row) for row in self.events()]
        before_cash = self.scalar(
            "SELECT cash_balance_usd FROM portfolios WHERE id = 1"
        )
        self.status()
        self.status()
        self.assertEqual(
            [dict(row) for row in self.events()], before_events
        )
        self.assertEqual(
            self.scalar("SELECT cash_balance_usd FROM portfolios WHERE id = 1"),
            before_cash,
        )

    def test_pass_does_not_claim_function_or_history_verification(self):
        self.install()
        result = self.status()
        for field in (
            "database_writes",
            "function_bodies_verified",
            "historical_completeness_verified",
            "execution_authorized",
        ):
            self.assertFalse(result[field])

    def test_invalid_schema_names_are_rejected(self):
        for schema in ("", "public; DROP TABLE portfolios", "pg_catalog", None):
            with self.subTest(schema=schema):
                with self.assertRaises(ValueError):
                    get_portfolio_audit_status(
                        schema=schema,
                        database_engine=self.engine,
                    )


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        PortfolioAuditStatusTests
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
