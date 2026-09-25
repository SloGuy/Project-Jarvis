"""Opt-in audit tests using actual models and accounting operations."""

import json
import os
from decimal import Decimal
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.market_db.database import engine as application_engine
from app.market_db.models import (
    MarketAsset,
    Portfolio,
    PortfolioPosition,
    PortfolioTransaction,
)
from app.market_db import paper_trading
from app.market_db.migrate_portfolio_audit import migrate_portfolio_audit


@unittest.skipUnless(
    os.getenv("PORTFOLIO_AUDIT_POSTGRES_TEST") == "1",
    "Set PORTFOLIO_AUDIT_POSTGRES_TEST=1 for isolated PostgreSQL tests.",
)
class AccountingAuditTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(application_engine.dialect.name, "postgresql")
        self.schema = "accounting_audit_test_" + uuid4().hex

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

        for model in (
            MarketAsset,
            Portfolio,
            PortfolioPosition,
            PortfolioTransaction,
        ):
            model.__table__.create(self.engine)

        with self.engine.begin() as connection:
            connection.execute(MarketAsset.__table__.insert().values(
                id=7,
                symbol="BTC",
                asset_type="crypto",
                provider_id="bitcoin",
                is_active=True,
            ))
            connection.execute(Portfolio.__table__.insert().values(
                id=1,
                name="Audit Test Paper",
                portfolio_type="paper",
                cash_balance_usd=Decimal("899"),
                is_active=True,
            ))
            connection.execute(PortfolioPosition.__table__.insert().values(
                id=10,
                portfolio_id=1,
                asset_id=7,
                quantity=Decimal("2"),
                average_cost_usd=Decimal("50.5"),
            ))
            connection.execute(PortfolioTransaction.__table__.insert().values(
                id=20,
                portfolio_id=1,
                asset_id=7,
                transaction_type="buy",
                quantity=Decimal("2"),
                price_usd=Decimal("50"),
                total_usd=Decimal("100"),
                fees_usd=Decimal("1"),
            ))

        migrate_portfolio_audit(
            schema=self.schema,
            database_engine=self.engine,
        )
        sessions = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )
        patched = patch.object(paper_trading, "SessionLocal", sessions)
        patched.start()
        self.addCleanup(patched.stop)

    def drop_schema(self):
        prefix = "accounting_audit_test_"
        suffix = self.schema.removeprefix(prefix)
        if (
            not self.schema.startswith(prefix)
            or len(suffix) != 32
            or any(character not in "0123456789abcdef" for character in suffix)
        ):
            raise RuntimeError("Unexpected test schema name.")
        with application_engine.begin() as connection:
            connection.execute(DropSchema(self.schema, cascade=True))

    def changes(self):
        with self.engine.connect() as connection:
            return connection.execute(text(
                "SELECT * FROM capital_accounting_audit "
                "WHERE operation <> 'BASELINE' ORDER BY event_id"
            )).mappings().all()

    def cash(self):
        with self.engine.connect() as connection:
            return connection.scalar(
                select(Portfolio.cash_balance_usd).where(Portfolio.id == 1)
            )

    def test_actual_reset_preserves_deleted_accounting_rows(self):
        paper_trading.reset_paper_portfolio(
            portfolio_id=1,
            starting_cash_usd=Decimal("1000"),
        )

        events = self.changes()
        deleted = {
            row["source_table"]: row
            for row in events if row["operation"] == "DELETE"
        }
        self.assertEqual(
            set(deleted), {"portfolio_positions", "portfolio_transactions"}
        )
        transaction = json.loads(
            deleted["portfolio_transactions"]["old_row_json"],
            parse_float=Decimal,
        )
        self.assertEqual(transaction["transaction_type"], "buy")
        self.assertEqual(transaction["total_usd"], Decimal("100"))
        self.assertEqual(self.cash(), Decimal("1000"))
        self.assertEqual(len({row["database_transaction_id"] for row in events}), 1)

        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text(
                "SELECT count(*) FROM portfolio_transactions"
            )), 0)
            self.assertEqual(connection.scalar(text(
                "SELECT count(*) FROM portfolio_positions"
            )), 0)

    def test_actual_deposit_records_cash_and_transaction(self):
        paper_trading.deposit_cash(
            portfolio_id=1,
            amount_usd=Decimal("25"),
        )
        self.assertEqual(self.cash(), Decimal("924"))
        events = self.changes()
        transaction = next(
            row for row in events
            if row["source_table"] == "portfolio_transactions"
            and row["operation"] == "INSERT"
        )
        image = json.loads(transaction["new_row_json"], parse_float=Decimal)
        self.assertEqual(image["transaction_type"], "deposit")
        self.assertEqual(image["total_usd"], Decimal("25"))
        self.assertIsNone(image["asset_id"])
        self.assertTrue(any(
            row["source_table"] == "portfolios"
            and row["operation"] == "UPDATE"
            for row in events
        ))
        self.assertEqual(len({row["database_transaction_id"] for row in events}), 1)

    def test_actual_withdrawal_records_external_cash_flow(self):
        paper_trading.withdraw_cash(
            portfolio_id=1,
            amount_usd=Decimal("25"),
        )
        self.assertEqual(self.cash(), Decimal("874"))
        transaction = next(
            row for row in self.changes()
            if row["source_table"] == "portfolio_transactions"
            and row["operation"] == "INSERT"
        )
        image = json.loads(transaction["new_row_json"], parse_float=Decimal)
        self.assertEqual(image["transaction_type"], "withdrawal")
        self.assertEqual(image["total_usd"], Decimal("25"))

    def test_rejected_withdrawal_leaves_no_audit_changes(self):
        with self.assertRaises(paper_trading.PaperTradingError):
            paper_trading.withdraw_cash(
                portfolio_id=1,
                amount_usd=Decimal("10000"),
            )
        self.assertEqual(self.cash(), Decimal("899"))
        self.assertEqual(self.changes(), [])


if __name__ == "__main__":
    unittest.main()
