from decimal import Decimal

from sqlalchemy import select

from app.autonomous_trading.mean_reversion_v2_exit import (
    frozen_recovery_target,
)
from app.market_db.database import SessionLocal
from app.market_db.models import AutonomousTradeJournal


def load_entry_exit_context(
    *,
    portfolio_id: int,
    asset_id: int,
):
    with SessionLocal() as session:
        journal = session.execute(
            select(
                AutonomousTradeJournal.entry_market_context,
                AutonomousTradeJournal.opened_at,
            ).where(
                AutonomousTradeJournal.portfolio_id == portfolio_id,
                AutonomousTradeJournal.asset_id == asset_id,
                AutonomousTradeJournal.strategy_name
                == "mean_reversion_v2",
                AutonomousTradeJournal.status == "open",
            )
        ).one_or_none()

    if journal is None:
        raise ValueError("Open V2 entry journal is missing.")

    context, opened_at = journal
    context = context or {}

    entry_mean = context.get("mean_price_usd")
    entry_std = context.get("standard_deviation_usd")

    if entry_mean is None or entry_std is None:
        raise ValueError("V2 entry mean or deviation is missing.")

    target = frozen_recovery_target(
        entry_mean=Decimal(str(entry_mean)),
        entry_std=Decimal(str(entry_std)),
    )

    return target, opened_at
