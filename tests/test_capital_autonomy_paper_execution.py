"""Opt-in tests of lifecycle guards inside real paper transactions."""

import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import func, select

from app.market_db import paper_trading as trading
from app.market_db.models import (
    MarketAsset,
    Portfolio,
    PortfolioPosition,
    PortfolioTransaction,
)
from app.capital import paper_execution_guard as guard
from app.capital.autonomy_policy import CapitalOperatingPolicy
from app.capital.paper_lifecycle_store import PaperLifecycleRecord
import test_capital_autonomy_paper_lifecycle as fixtures


@unittest.skipUnless(
    os.environ.get("PAPER_LIFECYCLE_POSTGRES_TEST") == "1",
    "Set PAPER_LIFECYCLE_POSTGRES_TEST=1 for isolated database tests.",
)
class ManagedPaperExecutionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AutonomousPaperLifecycleTests(
            methodName="test_activation_preserves_cash_and_creation_evidence"
        )
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.sessions = self.fixture.sessions
        with self.fixture.fixture.db.begin() as connection:
            PortfolioTransaction.__table__.create(connection)
        with self.sessions.begin() as session:
            session.add(MarketAsset(
                symbol="BTC", asset_type="crypto", is_active=True
            ))
            session.add(MarketAsset(
                symbol="ETH", asset_type="crypto", is_active=True
            ))
            self.portfolio_id = session.get(
                PaperLifecycleRecord, "request-1"
            ).portfolio_id
        self.fixture.change()
        self.patch(trading, "SessionLocal", self.sessions)
        self.control = self.patch(
            guard, "read_operating_policy",
            return_value=CapitalOperatingPolicy(enabled=True),
        )
        self.patch(trading, "_resolve_execution_price", return_value={
            "price_usd": Decimal("100"),
            "provider": "isolated-test",
            "observed_at": datetime.now(timezone.utc),
            "age_seconds": 0,
            "source": "test",
        })

    def patch(self, module, name, *args, **kwargs):
        patcher = patch.object(module, name, *args, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def buy(self, quantity="1", symbol="BTC", fees="0"):
        return trading.buy_asset(
            symbol=symbol,
            quantity=quantity,
            fees_usd=fees,
            portfolio_id=self.portfolio_id,
        )

    def sell(self, quantity="1"):
        return trading.sell_asset(
            symbol="BTC",
            quantity=quantity,
            portfolio_id=self.portfolio_id,
        )

    def snapshot(self):
        with self.sessions() as session:
            portfolio = session.get(Portfolio, self.portfolio_id)
            positions = session.scalars(
                select(PortfolioPosition)
                .where(PortfolioPosition.portfolio_id == self.portfolio_id)
                .order_by(PortfolioPosition.id)
            ).all()
            return {
                "cash": portfolio.cash_balance_usd,
                "positions": [
                    (row.asset_id, row.quantity, row.average_cost_usd)
                    for row in positions
                ],
                "transactions": session.scalar(
                    select(func.count()).select_from(PortfolioTransaction)
                    .where(
                        PortfolioTransaction.portfolio_id == self.portfolio_id
                    )
                ),
            }

    def test_active_buy_updates_cash_position_and_transaction(self):
        result = self.buy()
        self.assertEqual(result["status"], "success")
        after = self.snapshot()
        self.assertEqual(after["cash"], Decimal("900"))
        self.assertEqual(after["positions"][0][1], Decimal("1"))
        self.assertEqual(after["transactions"], 1)

    def test_paused_account_blocks_buy_and_allows_exit(self):
        self.buy()
        self.fixture.change(target="paused", version=2, key="pause")
        before = self.snapshot()
        with self.assertRaises(PermissionError):
            self.buy()
        self.assertEqual(self.snapshot(), before)
        self.sell()
        self.assertEqual(self.snapshot()["cash"], Decimal("1000"))
        self.assertEqual(self.snapshot()["positions"][0][1], 0)

    def test_demoted_account_blocks_buy_and_allows_exit(self):
        self.buy()
        self.fixture.change(target="demoted", version=2, key="demote")
        before = self.snapshot()
        with self.assertRaises(PermissionError):
            self.buy()
        self.assertEqual(self.snapshot(), before)
        self.sell()
        self.assertEqual(self.snapshot()["positions"][0][1], 0)

    def test_global_controls_block_entries_but_allow_exits(self):
        self.buy()
        for policy in (
            CapitalOperatingPolicy(enabled=False),
            CapitalOperatingPolicy(enabled=True, paused=True),
        ):
            with self.subTest(policy=policy):
                self.control.return_value = policy
                before = self.snapshot()
                with self.assertRaises(PermissionError):
                    self.buy()
                self.assertEqual(self.snapshot(), before)
        self.sell()
        self.assertEqual(self.snapshot()["cash"], Decimal("1000"))

    def test_unvalidated_asset_is_rejected_without_writes(self):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "BTC universe"):
            self.buy(symbol="ETH")
        self.assertEqual(self.snapshot(), before)

    def test_allocation_includes_existing_cost_and_new_fees(self):
        self.buy(quantity="5")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "allocation"):
            self.buy(quantity="5", fees="1")
        self.assertEqual(self.snapshot(), before)
        self.buy(quantity="5")
        self.assertEqual(self.snapshot()["cash"], 0)

    def test_direct_cash_changes_and_reset_are_blocked(self):
        self.buy()
        operations = (
            lambda: trading.deposit_cash(100, self.portfolio_id),
            lambda: trading.withdraw_cash(100, self.portfolio_id),
            lambda: trading.reset_paper_portfolio(
                portfolio_id=self.portfolio_id
            ),
        )
        before = self.snapshot()
        for operation in operations:
            with self.assertRaisesRegex(ValueError, "cash changes or resets"):
                operation()
            self.assertEqual(self.snapshot(), before)

    def test_unmanaged_paper_portfolio_keeps_existing_behavior(self):
        with self.sessions.begin() as session:
            portfolio = Portfolio(
                name="Isolated legacy account",
                portfolio_type="paper",
                cash_balance_usd=Decimal("1000"),
                is_active=True,
            )
            session.add(portfolio)
            session.flush()
            portfolio_id = portfolio.id
        self.control.return_value = CapitalOperatingPolicy(enabled=False)
        result = trading.buy_asset(
            symbol="ETH", quantity=1, portfolio_id=portfolio_id
        )
        self.assertEqual(result["status"], "success")


if __name__ == "__main__":
    unittest.main()
