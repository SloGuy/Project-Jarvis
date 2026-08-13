from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import (
    AutonomousTradeDecision,
    AutonomousTradeJournal,
    MarketAsset,
    PortfolioTransaction,
)


class TradeJournalError(ValueError):
    """Raised when an autonomous trade journal entry cannot be created."""


def open_trade_journal(
    *,
    decision_id: int,
    transaction_id: int,
    entry_market_context: dict[str, Any],
    expected_outcome: str | None = None,
) -> AutonomousTradeJournal:
    """
    Open a journal lifecycle record for an executed autonomous BUY.

    This function records an already-executed trade.
    It does not execute trades or approve risk.
    """

    with SessionLocal() as session:
        decision = session.scalar(
            select(AutonomousTradeDecision)
            .where(
                AutonomousTradeDecision.id == decision_id,
            )
        )

        if decision is None:
            raise TradeJournalError(
                f"Autonomous decision {decision_id} was not found."
            )

        if decision.action != "buy":
            raise TradeJournalError(
                "Only executed BUY decisions can open a trade journal."
            )

        if decision.execution_status != "executed":
            raise TradeJournalError(
                "The BUY decision has not been executed."
            )

        transaction = session.scalar(
            select(PortfolioTransaction)
            .where(
                PortfolioTransaction.id == transaction_id,
            )
        )

        if transaction is None:
            raise TradeJournalError(
                f"Portfolio transaction {transaction_id} was not found."
            )

        if transaction.transaction_type != "buy":
            raise TradeJournalError(
                "The entry transaction must be a BUY transaction."
            )

        if transaction.asset_id != decision.asset_id:
            raise TradeJournalError(
                "Decision and transaction asset IDs do not match."
            )

        existing = session.scalar(
            select(AutonomousTradeJournal)
            .where(
                AutonomousTradeJournal.entry_decision_id
                == decision.id,
            )
        )

        if existing is not None:
            return existing

        asset = session.scalar(
            select(MarketAsset)
            .where(
                MarketAsset.id == decision.asset_id,
            )
        )

        if asset is None:
            raise TradeJournalError(
                f"Market asset {decision.asset_id} was not found."
            )

        opened_at: datetime = transaction.created_at

        record = AutonomousTradeJournal(
            portfolio_id=decision.portfolio_id,
            asset_id=decision.asset_id,
            status="open",
            strategy_name=decision.strategy_name,
            entry_decision_id=decision.id,
            entry_transaction_id=transaction.id,
            entry_quantity=Decimal(transaction.quantity),
            entry_price_usd=Decimal(transaction.price_usd),
            entry_confidence_percent=Decimal(
                decision.confidence_percent
            ),
            entry_rationale=decision.rationale,
            entry_market_context=dict(entry_market_context),
            expected_outcome=(
                expected_outcome.strip()
                if expected_outcome
                else None
            ),
            opened_at=opened_at,
            created_at=opened_at,
            updated_at=opened_at,
        )

        session.add(record)
        session.commit()
        session.refresh(record)

        return record


def close_trade_journal(
    *,
    decision_id: int,
    transaction_id: int,
    exit_rule: str | None,
    exit_market_context: dict[str, Any],
) -> AutonomousTradeJournal:
    """
    Close the open journal lifecycle record for an executed autonomous SELL.

    This function records an already-executed exit.
    It does not execute trades or approve risk.
    """

    with SessionLocal() as session:
        decision = session.scalar(
            select(AutonomousTradeDecision)
            .where(
                AutonomousTradeDecision.id == decision_id,
            )
        )

        if decision is None:
            raise TradeJournalError(
                f"Autonomous decision {decision_id} was not found."
            )

        if decision.action != "sell":
            raise TradeJournalError(
                "Only executed SELL decisions can close a trade journal."
            )

        if decision.execution_status != "executed":
            raise TradeJournalError(
                "The SELL decision has not been executed."
            )

        transaction = session.scalar(
            select(PortfolioTransaction)
            .where(
                PortfolioTransaction.id == transaction_id,
            )
        )

        if transaction is None:
            raise TradeJournalError(
                f"Portfolio transaction {transaction_id} was not found."
            )

        if transaction.transaction_type != "sell":
            raise TradeJournalError(
                "The exit transaction must be a SELL transaction."
            )

        if transaction.asset_id != decision.asset_id:
            raise TradeJournalError(
                "Decision and transaction asset IDs do not match."
            )

        existing_closed = session.scalar(
            select(AutonomousTradeJournal)
            .where(
                AutonomousTradeJournal.exit_decision_id
                == decision.id,
            )
        )

        if existing_closed is not None:
            return existing_closed

        journal = session.scalar(
            select(AutonomousTradeJournal)
            .where(
                AutonomousTradeJournal.portfolio_id
                == decision.portfolio_id,
                AutonomousTradeJournal.asset_id
                == decision.asset_id,
                AutonomousTradeJournal.status == "open",
            )
            .order_by(
                AutonomousTradeJournal.opened_at.asc()
            )
            .limit(1)
        )

        if journal is None:
            raise TradeJournalError(
                "No open trade journal exists for this SELL."
            )

        closed_at = transaction.created_at

        holding_duration = (
            closed_at - journal.opened_at
        )

        realized_gain_loss_usd = Decimal(
            transaction.realized_gain_loss_usd
            or 0
        )

        entry_value = (
            Decimal(journal.entry_quantity)
            * Decimal(journal.entry_price_usd)
        )

        return_percent = None

        if entry_value > Decimal("0"):
            return_percent = (
                realized_gain_loss_usd
                / entry_value
                * Decimal("100")
            )

        if realized_gain_loss_usd > Decimal("0"):
            actual_outcome = "profitable"
            thesis_correct = True
        elif realized_gain_loss_usd < Decimal("0"):
            actual_outcome = "unprofitable"
            thesis_correct = False
        else:
            actual_outcome = "breakeven"
            thesis_correct = None

        journal.status = "closed"
        journal.exit_decision_id = decision.id
        journal.exit_transaction_id = transaction.id
        journal.exit_price_usd = Decimal(
            transaction.price_usd
        )
        journal.exit_rationale = decision.rationale
        journal.exit_rule = (
            exit_rule.strip()
            if exit_rule
            else None
        )
        journal.exit_market_context = dict(
            exit_market_context
        )
        journal.closed_at = closed_at
        journal.holding_duration_seconds = int(
            holding_duration.total_seconds()
        )
        journal.realized_gain_loss_usd = (
            realized_gain_loss_usd
        )
        journal.return_percent = return_percent
        journal.actual_outcome = actual_outcome
        journal.thesis_correct = thesis_correct
        journal.updated_at = closed_at

        session.commit()
        session.refresh(journal)

        return journal
