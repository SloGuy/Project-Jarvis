"""Asset metadata tests in disposable PostgreSQL schemas."""
import json
import os
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
class AuditReaderAssetTests(unittest.TestCase):
    drop_schema = fixtures.PortfolioAuditPostgresTests.drop_schema
    install = fixtures.PortfolioAuditPostgresTests.install
    scalar = fixtures.PortfolioAuditPostgresTests.scalar

    def setUp(self):
        fixtures.PortfolioAuditPostgresTests.setUp(self)
        with self.engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE market_assets (
                    id BIGINT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL
                )
            """))
            connection.execute(text("""
                INSERT INTO market_assets VALUES
                    (7, 'BTC', 'crypto', TRUE),
                    (8, 'ETH', 'crypto', TRUE)
            """))
        self.install()

    def read(self, **kwargs):
        return reader.read_portfolio_audit(
            schema=self.schema,
            database_engine=self.engine,
            **kwargs,
        )

    def test_metadata_is_opt_in(self):
        result = self.read()
        self.assertFalse(result["asset_metadata_requested"])
        self.assertEqual(result["asset_rows"], [])

    def test_only_positive_held_assets_are_read(self):
        result = self.read(include_assets=True)
        self.assertTrue(result["asset_metadata_requested"])
        self.assertEqual(len(result["asset_rows"]), 1)
        row = result["asset_rows"][0]
        self.assertEqual(row["source_row_id"], 7)
        self.assertEqual(json.loads(row["row_json"])["symbol"], "BTC")

    def test_zero_quantity_does_not_require_metadata(self):
        with self.engine.begin() as connection:
            connection.execute(text(
                "UPDATE portfolio_positions SET quantity = 0 WHERE id = 10"
            ))
        self.assertEqual(self.read(include_assets=True)["asset_rows"], [])

    def test_inactive_metadata_is_retained_for_downstream_checks(self):
        with self.engine.begin() as connection:
            connection.execute(text(
                "UPDATE market_assets SET is_active = FALSE WHERE id = 7"
            ))
        result = self.read(include_assets=True)
        image = json.loads(result["asset_rows"][0]["row_json"])
        self.assertIs(image["is_active"], False)

    def test_missing_metadata_is_not_fabricated(self):
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM market_assets WHERE id = 7"))
        result = self.read(include_assets=True)
        self.assertEqual(result["asset_rows"], [])
        self.assertEqual(len(result["current_rows"]["portfolio_positions"]), 1)

    def test_concurrent_metadata_commit_is_excluded(self):
        original = reader._bounded_rows
        committed = False

        def read_then_change(connection, statement, maximum):
            nonlocal committed
            rows = original(connection, statement, maximum)
            if not committed:
                committed = True
                with self.engine.begin() as writer:
                    writer.execute(text(
                        "UPDATE market_assets SET symbol = 'CHANGED' WHERE id = 7"
                    ))
            return rows

        with patch.object(
            reader, "_bounded_rows", side_effect=read_then_change
        ):
            result = self.read(include_assets=True)

        self.assertTrue(committed)
        self.assertEqual(
            json.loads(result["asset_rows"][0]["row_json"])["symbol"], "BTC"
        )
        later = self.read(include_assets=True)
        self.assertEqual(
            json.loads(later["asset_rows"][0]["row_json"])["symbol"], "CHANGED"
        )

    def test_missing_asset_table_fails_when_requested(self):
        with self.engine.begin() as connection:
            connection.execute(text("DROP TABLE market_assets"))
        with self.assertRaises(DBAPIError):
            self.read(include_assets=True)
        self.assertEqual(self.read()["asset_rows"], [])

    def test_nonboolean_option_is_rejected(self):
        for value in (1, 0, "true", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.read(include_assets=value)

    def test_metadata_read_does_not_modify_accounting(self):
        before = self.scalar("SELECT count(*) FROM capital_accounting_audit")
        result = self.read(include_assets=True)
        self.assertEqual(
            self.scalar("SELECT count(*) FROM capital_accounting_audit"),
            before,
        )
        self.assertTrue(result["database_read_only"])
        self.assertFalse(result["database_writes"])


if __name__ == "__main__":
    unittest.main()
