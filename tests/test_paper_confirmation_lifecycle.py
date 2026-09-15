import ast
import tempfile
import types
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from enum import Enum
from unittest.mock import patch
from sqlalchemy import create_engine, select, Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

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

temp = tempfile.TemporaryDirectory()
url = "sqlite:///" + str(Path(temp.name) / "test.sqlite")
engine = create_engine(url)
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)
REQUIRED_CONFIRMATIONS = 3

source = Path("app/autonomous_trading/signal_confirmation.py")
tree = ast.parse(source.read_text())
nodes = [n for n in tree.body
         if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
scope = dict(globals())
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)
update = scope["update_signal_confirmation"]

models = types.ModuleType("app.market_db.models")
models.Portfolio = Portfolio
models.PortfolioTransaction = PortfolioTransaction
registry = types.ModuleType("app.capital.experiment_registry")
registry.list_experiments = lambda: [
    types.SimpleNamespace(strategy_name="mean_reversion_v2", portfolio_name="MR2")
]


NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)

def observe(seconds, strategy="mean_reversion_v2"):
    return update(
        symbol="BTC", strategy_name=strategy,
        action=StrategyAction.BUY,
        observation_at=NOW + timedelta(seconds=seconds),
    )

def fill(seconds, side="sell", rollback=False, portfolio=1):
    with SessionLocal() as session:
        session.add(PortfolioTransaction(
            portfolio_id=portfolio, asset_id=1,
            transaction_type=side,
            created_at=NOW + timedelta(seconds=seconds),
        ))
        session.flush()
        session.rollback() if rollback else session.commit()

with SessionLocal.begin() as session:
    session.add(MarketAsset(id=1, symbol="BTC", is_active=True))
    session.add(Portfolio(
        id=1, name="MR2", is_active=True, portfolio_type="paper"
    ))

with patch.dict("sys.modules", {
    "app.market_db.models": models,
    "app.capital.experiment_registry": registry,
}):
    assert not observe(1).confirmed
    assert not observe(2).confirmed
    assert observe(3).confirmed
    print("PASS: initial three observations")

    for cutoff, side in ((10, "buy"), (20, "sell")):
        fill(cutoff, side)
        for at in (cutoff - 1, cutoff):
            result = observe(at)
            assert not result.confirmed and not result.observation_counted
        assert observe(cutoff + 1).confirmation_count == 1
        duplicate = observe(cutoff + 1)
        assert not duplicate.confirmed and not duplicate.observation_counted
        assert not observe(cutoff + 2).confirmed
        assert observe(cutoff + 3).confirmed
        print("PASS:", side, "fill resets; old/duplicate inputs cannot count")

    fill(30, rollback=True)
    assert observe(31).confirmed
    print("PASS: rolled-back fill does not reset")

    fill(40, portfolio=2)
    assert observe(41).confirmed
    print("PASS: unrelated portfolio does not reset")

    fill(50)
    engine.dispose()
    engine = create_engine(url)
    SessionLocal = sessionmaker(bind=engine)
    scope["SessionLocal"] = SessionLocal
    assert not observe(50).confirmed
    assert observe(51).confirmation_count == 1
    assert not observe(52).confirmed
    assert observe(53).confirmed
    print("PASS: committed fill survives connection restart")

    fill(60)
    assert not observe(61, "other_strategy").confirmed
    assert not observe(62, "other_strategy").confirmed
    assert observe(63, "other_strategy").confirmed
    fill(64)
    assert observe(65, "other_strategy").confirmed
    print("PASS: other strategy behavior unchanged")

engine.dispose()
temp.cleanup()
print("PASS: isolated paper-confirmation tests")
