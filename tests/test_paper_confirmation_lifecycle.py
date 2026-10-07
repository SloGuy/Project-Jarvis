"""SQLite tests for persistent paper-confirmation lifecycle behavior."""

import ast
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    create_engine,
    select,
)
from sqlalchemy.orm import declarative_base, sessionmaker


Base = declarative_base()
REQUIRED_CONFIRMATIONS = 3
NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)


class MarketAsset(Base):
    __tablename__ = "assets"
    id = Column(Integer, primary_key=True)
    symbol = Column(String)
    is_active = Column(Boolean)


class Portfolio(Base):
    __tablename__ = "portfolios"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    is_active = Column(Boolean)
    portfolio_type = Column(String)


class PortfolioTransaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer)
    asset_id = Column(Integer)
    transaction_type = Column(String)
    created_at = Column(DateTime(timezone=True))


class AutonomousStrategyState(Base):
    __tablename__ = "states"
    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer)
    strategy_name = Column(String)
    pending_action = Column(String)
    confirmation_count = Column(Integer)
    first_confirmed_at = Column(DateTime(timezone=True))
    last_confirmed_at = Column(DateTime(timezone=True))
    last_observation_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class StrategyAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class PaperConfirmationLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.url = "sqlite:///" + str(
            Path(temporary.name) / "test.sqlite"
        )
        self.engine = create_engine(self.url)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

        source = Path(
            "app/autonomous_trading/signal_confirmation.py"
        )
        tree = ast.parse(source.read_text())
        nodes = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        ]

        self.scope = dict(globals())
        exec(
            compile(
                ast.Module(body=nodes, type_ignores=[]),
                str(source),
                "exec",
            ),
            self.scope,
        )

        # The extracted module includes its lazy session wrapper.
        # Bind this harness to its own SQLite factory.
        self.scope["SessionLocal"] = self.SessionLocal
        self.update = self.scope["update_signal_confirmation"]

        models = types.ModuleType("app.market_db.models")
        models.MarketAsset = MarketAsset
        models.AutonomousStrategyState = AutonomousStrategyState
        models.Portfolio = Portfolio
        models.PortfolioTransaction = PortfolioTransaction

        registry = types.ModuleType(
            "app.capital.experiment_registry"
        )
        registry.list_experiments = lambda: [
            types.SimpleNamespace(
                strategy_name="mean_reversion_v2",
                portfolio_name="MR2",
            )
        ]

        modules = patch.dict("sys.modules", {
            "app.market_db.models": models,
            "app.capital.experiment_registry": registry,
        })
        modules.start()
        self.addCleanup(modules.stop)

        with self.SessionLocal.begin() as session:
            session.add(MarketAsset(
                id=1, symbol="BTC", is_active=True
            ))
            session.add(Portfolio(
                id=1,
                name="MR2",
                is_active=True,
                portfolio_type="paper",
            ))

    def observe(self, seconds, strategy="mean_reversion_v2"):
        return self.update(
            symbol="BTC",
            strategy_name=strategy,
            action=StrategyAction.BUY,
            observation_at=NOW + timedelta(seconds=seconds),
        )

    def fill(
        self,
        seconds,
        side="sell",
        rollback=False,
        portfolio=1,
    ):
        with self.SessionLocal() as session:
            session.add(PortfolioTransaction(
                portfolio_id=portfolio,
                asset_id=1,
                transaction_type=side,
                created_at=NOW + timedelta(seconds=seconds),
            ))
            session.flush()
            if rollback:
                session.rollback()
            else:
                session.commit()

    def confirm_initial(self):
        self.assertFalse(self.observe(1).confirmed)
        self.assertFalse(self.observe(2).confirmed)
        self.assertTrue(self.observe(3).confirmed)

    def test_initial_signal_requires_three_observations(self):
        self.confirm_initial()

    def test_buy_and_sell_fills_reset_confirmation(self):
        self.confirm_initial()

        for cutoff, side in ((10, "buy"), (20, "sell")):
            with self.subTest(side=side):
                self.fill(cutoff, side)

                for at in (cutoff - 1, cutoff):
                    result = self.observe(at)
                    self.assertFalse(result.confirmed)
                    self.assertFalse(result.observation_counted)

                self.assertEqual(
                    self.observe(cutoff + 1).confirmation_count,
                    1,
                )
                duplicate = self.observe(cutoff + 1)
                self.assertFalse(duplicate.confirmed)
                self.assertFalse(duplicate.observation_counted)
                self.assertFalse(self.observe(cutoff + 2).confirmed)
                self.assertTrue(self.observe(cutoff + 3).confirmed)

    def test_rolled_back_fill_does_not_reset(self):
        self.confirm_initial()
        self.fill(30, rollback=True)

        self.assertTrue(self.observe(31).confirmed)

    def test_unrelated_portfolio_fill_does_not_reset(self):
        self.confirm_initial()
        self.fill(40, portfolio=2)

        self.assertTrue(self.observe(41).confirmed)

    def test_committed_fill_survives_connection_restart(self):
        self.confirm_initial()
        self.fill(50)

        self.engine.dispose()
        self.engine = create_engine(self.url)
        self.addCleanup(self.engine.dispose)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.scope["SessionLocal"] = self.SessionLocal

        self.assertFalse(self.observe(50).confirmed)
        self.assertEqual(
            self.observe(51).confirmation_count, 1
        )
        self.assertFalse(self.observe(52).confirmed)
        self.assertTrue(self.observe(53).confirmed)

    def test_other_strategy_behavior_is_preserved(self):
        self.fill(60)

        self.assertFalse(
            self.observe(61, "other_strategy").confirmed
        )
        self.assertFalse(
            self.observe(62, "other_strategy").confirmed
        )
        self.assertTrue(
            self.observe(63, "other_strategy").confirmed
        )

        self.fill(64)
        self.assertTrue(
            self.observe(65, "other_strategy").confirmed
        )


if __name__ == "__main__":
    unittest.main()
