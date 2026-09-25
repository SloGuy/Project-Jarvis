"""Opt-in migration concurrency tests using disposable PostgreSQL schemas."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from unittest.mock import patch

from sqlalchemy import text

from app.market_db import migrate_portfolio_audit as migration
from test_portfolio_audit_postgres import PortfolioAuditPostgresTests


class PortfolioAuditConcurrencyTests(unittest.TestCase):
    # Reuse only the isolated fixture, not the other test methods.
    setUp = PortfolioAuditPostgresTests.setUp
    drop_schema = PortfolioAuditPostgresTests.drop_schema
    install = PortfolioAuditPostgresTests.install
    scalar = PortfolioAuditPostgresTests.scalar
    events = PortfolioAuditPostgresTests.events

    __unittest_skip__ = getattr(PortfolioAuditPostgresTests, "__unittest_skip__", False)
    __unittest_skip_why__ = getattr(PortfolioAuditPostgresTests, "__unittest_skip_why__", "")

    def test_write_after_baseline_waits_then_is_audited(self):
        baseline_ready = Event()
        release_migration = Event()
        writer_started = Event()
        original = migration._execute_sql_file

        def pause_before_triggers(connection, filename):
            if filename == "portfolio_audit_triggers.sql":
                baseline_ready.set()
                if not release_migration.wait(timeout=8):
                    raise RuntimeError("Test migration release timed out.")
            return original(connection, filename)

        def write():
            with self.engine.begin() as connection:
                writer_started.set()
                connection.execute(text(
                    "UPDATE portfolios SET cash_balance_usd = 777 WHERE id = 1"
                ))

        with patch.object(
            migration, "_execute_sql_file", side_effect=pause_before_triggers
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                migration_future = pool.submit(self.install)
                try:
                    self.assertTrue(baseline_ready.wait(timeout=8))
                    writer_future = pool.submit(write)
                    self.assertTrue(writer_started.wait(timeout=3))
                    # Release the migration; the write must either have waited
                    # on its source lock or execute after installation commits.
                finally:
                    release_migration.set()

                migration_future.result(timeout=15)
                writer_future.result(timeout=15)

        updates = self.events("UPDATE")
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["source_table"], "portfolios")
        self.assertEqual(
            self.scalar("SELECT cash_balance_usd FROM portfolios WHERE id = 1"),
            777,
        )

        import json
        from decimal import Decimal

        baseline = next(
            row for row in self.events("BASELINE")
            if row["source_table"] == "portfolios"
        )
        before = json.loads(baseline["new_row_json"], parse_float=Decimal)
        self.assertEqual(before["cash_balance_usd"], Decimal("899.00000001"))

    def test_write_committed_before_lock_is_in_baseline(self):
        writer_updated = Event()
        release_writer = Event()
        migration_started = Event()

        def write():
            with self.engine.begin() as connection:
                connection.execute(text(
                    "UPDATE portfolios SET cash_balance_usd = 888 WHERE id = 1"
                ))
                writer_updated.set()
                if not release_writer.wait(timeout=8):
                    raise RuntimeError("Test writer release timed out.")

        def install():
            migration_started.set()
            return self.install()

        with ThreadPoolExecutor(max_workers=2) as pool:
            writer_future = pool.submit(write)
            try:
                self.assertTrue(writer_updated.wait(timeout=8))
                migration_future = pool.submit(install)
                self.assertTrue(migration_started.wait(timeout=3))
            finally:
                release_writer.set()

            writer_future.result(timeout=15)
            migration_future.result(timeout=15)

        import json
        from decimal import Decimal

        baseline = next(
            row for row in self.events("BASELINE")
            if row["source_table"] == "portfolios"
        )
        image = json.loads(baseline["new_row_json"], parse_float=Decimal)
        self.assertEqual(image["cash_balance_usd"], Decimal("888"))
        self.assertEqual(self.events("UPDATE"), [])

    def test_concurrent_installers_produce_one_installation(self):
        def attempt():
            try:
                return self.install()["status"]
            except RuntimeError as error:
                if "already exist" not in str(error):
                    raise
                return "already_exists"

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]

        self.assertEqual(sorted(results), ["already_exists", "installed"])
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM capital_accounting_audit_installation"
            ),
            1,
        )
        self.assertEqual(len(self.events("BASELINE")), 3)


if __name__ == "__main__":
    # Load only this class; the imported fixture class is not rerun here.
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        PortfolioAuditConcurrencyTests
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
