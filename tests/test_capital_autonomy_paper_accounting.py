"""Opt-in reconciliation tests using isolated paper accounting."""

import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import func, select

from app.capital import autonomy_paper_accounting as accounting
from app.market_db.models import (
    Portfolio,
    PortfolioPosition,
    PortfolioTransaction,
)
import test_capital_autonomy_paper_execution as fixtures


@unittest.skipUnless(
    os.environ.get("PAPER_LIFECYCLE_POSTGRES_TEST") == "1",
    "Set PAPER_LIFECYCLE_POSTGRES_TEST=1 for isolated database tests.",
)
class PaperAccountingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ManagedPaperExecutionTests(
            methodName="test_active_buy_updates_cash_position_and_transaction"
        )
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.sessions = self.fixture.sessions
        patcher = patch.object(accounting, "SessionLocal", self.sessions)
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self):
        return accounting.read_paper_accounting("request-1")

    def sell_row(self, session):
        return session.scalar(
            select(PortfolioTransaction).where(
                PortfolioTransaction.portfolio_id == self.fixture.portfolio_id,
                PortfolioTransaction.transaction_type == "sell",
            )
        )

    def test_new_account_reconciles(self):
        result = self.read()
        self.assertTrue(result["accounting_valid"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["transaction_count"], 0)
        self.assertEqual(result["sell_fill_count"], 0)
        self.assertEqual(result["holding_count"], 0)
        self.assertEqual(Decimal(result["realized_gain_loss_usd"]), 0)

    def test_buy_reconciles_cash_and_quantity(self):
        self.fixture.buy(quantity="2", fees="1")
        result = self.read()
        self.assertTrue(result["accounting_valid"])
        self.assertEqual(result["transaction_count"], 1)
        self.assertEqual(result["holding_count"], 1)
        self.assertEqual(result["sell_fill_count"], 0)

    def test_partial_sell_reconciles(self):
        self.fixture.buy(quantity="2")
        self.fixture.sell(quantity="1")
        result = self.read()
        self.assertTrue(result["accounting_valid"])
        self.assertEqual(result["holding_count"], 1)
        self.assertEqual(result["sell_fill_count"], 1)

    def test_full_sell_preserves_realized_fees(self):
        self.fixture.buy(quantity="2", fees="1")
        self.fixture.sell(quantity="2")
        result = self.read()
        self.assertTrue(result["accounting_valid"])
        self.assertEqual(result["holding_count"], 0)
        self.assertEqual(result["sell_fill_count"], 1)
        self.assertEqual(Decimal(result["realized_gain_loss_usd"]), -1)

    def test_cash_mismatch_is_reported(self):
        with self.sessions.begin() as session:
            portfolio = session.get(Portfolio, self.fixture.portfolio_id)
            portfolio.cash_balance_usd = Decimal("999")
        result = self.read()
        self.assertFalse(result["accounting_valid"])
        self.assertIn(
            "Cash does not match the trade ledger.", result["issues"]
        )

    def test_quantity_mismatch_is_reported(self):
        self.fixture.buy()
        with self.sessions.begin() as session:
            position = session.scalar(
                select(PortfolioPosition).where(
                    PortfolioPosition.portfolio_id == self.fixture.portfolio_id
                )
            )
            position.quantity = Decimal("2")
        result = self.read()
        self.assertFalse(result["accounting_valid"])
        self.assertTrue(any(
            "Quantity mismatch" in issue for issue in result["issues"]
        ))

    def test_missing_realized_result_is_not_treated_as_zero(self):
        self.fixture.buy()
        self.fixture.sell()
        with self.sessions.begin() as session:
            self.sell_row(session).realized_gain_loss_usd = None
        result = self.read()
        self.assertFalse(result["accounting_valid"])
        self.assertTrue(any(
            "Invalid accounting fields" in issue for issue in result["issues"]
        ))

    def test_nonfinite_realized_result_is_rejected(self):
        self.fixture.buy()
        self.fixture.sell()
        with self.sessions.begin() as session:
            self.sell_row(session).realized_gain_loss_usd = Decimal("NaN")
        self.assertFalse(self.read()["accounting_valid"])

    def test_nontrade_transaction_is_reported(self):
        with self.sessions.begin() as session:
            session.add(PortfolioTransaction(
                portfolio_id=self.fixture.portfolio_id,
                asset_id=None,
                transaction_type="deposit",
                quantity=1,
                price_usd=1,
                total_usd=1,
                fees_usd=0,
            ))
        result = self.read()
        self.assertFalse(result["accounting_valid"])
        self.assertTrue(any(
            "Unsupported transaction" in issue for issue in result["issues"]
        ))

    def test_reader_does_not_change_accounting(self):
        self.fixture.buy()
        before = self.fixture.snapshot()
        result = self.read()
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertIs(result["database_writes"], False)
        self.assertIs(result["historical_completeness_verified"], False)
        with self.sessions() as session:
            count = session.scalar(
                select(func.count()).select_from(PortfolioTransaction)
            )
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
