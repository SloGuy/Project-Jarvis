from typing import Any

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import (
    AutonomousTradeJournal,
    MarketAsset,
)


def _learning_classification(
    *,
    actual_outcome: str | None,
    thesis_correct: bool | None,
) -> str:
    if actual_outcome is None:
        return "open"

    if thesis_correct is None:
        return "inconclusive"

    if (
        actual_outcome == "profitable"
        and thesis_correct
    ):
        return "strong_success"

    if (
        actual_outcome == "profitable"
        and not thesis_correct
    ):
        return "profitable_despite_bad_thesis"

    if (
        actual_outcome == "unprofitable"
        and thesis_correct
    ):
        return "thesis_right_trade_lost"

    if (
        actual_outcome == "unprofitable"
        and not thesis_correct
    ):
        return "full_thesis_failure"

    if actual_outcome == "breakeven":
        return "breakeven"

    return "inconclusive"


def _serialize_journal_row(
    *,
    journal: AutonomousTradeJournal,
    symbol: str,
) -> dict[str, Any]:
    return {
        "id": journal.id,
        "symbol": symbol,
        "status": journal.status,
        "strategy_name": journal.strategy_name,
        "entry_decision_id": journal.entry_decision_id,
        "entry_transaction_id": journal.entry_transaction_id,
        "entry_quantity": float(journal.entry_quantity),
        "entry_price_usd": float(journal.entry_price_usd),
        "entry_confidence_percent": float(
            journal.entry_confidence_percent
        ),
        "entry_rationale": journal.entry_rationale,
        "entry_market_context": journal.entry_market_context,
        "expected_outcome": journal.expected_outcome,
        "opened_at": journal.opened_at.isoformat(),
        "exit_decision_id": journal.exit_decision_id,
        "exit_transaction_id": journal.exit_transaction_id,
        "exit_price_usd": (
            float(journal.exit_price_usd)
            if journal.exit_price_usd is not None
            else None
        ),
        "exit_rationale": journal.exit_rationale,
        "exit_rule": journal.exit_rule,
        "exit_market_context": journal.exit_market_context,
        "closed_at": (
            journal.closed_at.isoformat()
            if journal.closed_at is not None
            else None
        ),
        "holding_duration_seconds": (
            journal.holding_duration_seconds
        ),
        "realized_gain_loss_usd": (
            float(journal.realized_gain_loss_usd)
            if journal.realized_gain_loss_usd is not None
            else None
        ),
        "return_percent": (
            float(journal.return_percent)
            if journal.return_percent is not None
            else None
        ),
        "actual_outcome": journal.actual_outcome,
        "thesis_correct": journal.thesis_correct,
        "learning_classification": (
            _learning_classification(
                actual_outcome=journal.actual_outcome,
                thesis_correct=journal.thesis_correct,
            )
        ),
        "created_at": journal.created_at.isoformat(),
        "updated_at": journal.updated_at.isoformat(),
    }


def get_trade_journal(
    *,
    status: str | None = None,
    limit: int = 100,
    portfolio_id: int | None = None,
    strategy_name: str | None = None,
) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError(
            "limit must be greater than zero."
        )

    normalized_status = (
        status.strip().lower()
        if status
        else None
    )

    if normalized_status not in (
        None,
        "open",
        "closed",
    ):
        raise ValueError(
            "status must be open, closed, or None."
        )

    with SessionLocal() as session:
        query = (
            select(
                AutonomousTradeJournal,
                MarketAsset.symbol,
            )
            .join(
                MarketAsset,
                MarketAsset.id
                == AutonomousTradeJournal.asset_id,
            )
        )

        if normalized_status is not None:
            query = query.where(
                AutonomousTradeJournal.status
                == normalized_status
            )

        if portfolio_id is not None:
            query = query.where(
                AutonomousTradeJournal.portfolio_id
                == portfolio_id
            )

        if strategy_name is not None:
            normalized_strategy_name = (
                strategy_name.strip()
            )

            if not normalized_strategy_name:
                raise ValueError(
                    "strategy_name must not be empty."
                )

            query = query.where(
                AutonomousTradeJournal.strategy_name
                == normalized_strategy_name
            )

        rows = session.execute(
            query
            .order_by(
                AutonomousTradeJournal.opened_at.desc()
            )
            .limit(limit)
        ).all()

        journals = [
            _serialize_journal_row(
                journal=journal,
                symbol=str(symbol),
            )
            for journal, symbol in rows
        ]

    return {
        "status": "success",
        "filter": normalized_status,
        "portfolio_id": portfolio_id,
        "strategy_name": strategy_name,
        "count": len(journals),
        "journals": journals,
    }
