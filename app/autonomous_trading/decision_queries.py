from typing import Any

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import (
    AutonomousTradeDecision,
    MarketAsset,
)


def get_recent_trade_decisions(
    *,
    limit: int = 20,
    approved: bool | None = None,
    portfolio_id: int | None = None,
    strategy_name: str | None = None,
) -> list[dict[str, Any]]:
    """
    Return recent autonomous trade decisions.

    This function reads decision history only.
    It does not evaluate or execute trades.
    """

    with SessionLocal() as session:
        statement = (
            select(
                AutonomousTradeDecision,
                MarketAsset,
            )
            .join(
                MarketAsset,
                MarketAsset.id
                == AutonomousTradeDecision.asset_id,
            )
            .order_by(
                AutonomousTradeDecision.created_at.desc(),
                AutonomousTradeDecision.id.desc(),
            )
            .limit(limit)
        )

        if approved is not None:
            statement = statement.where(
                AutonomousTradeDecision.approved
                .is_(approved)
            )

        if portfolio_id is not None:
            statement = statement.where(
                AutonomousTradeDecision.portfolio_id
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

            statement = statement.where(
                AutonomousTradeDecision.strategy_name
                == normalized_strategy_name
            )

        rows = session.execute(statement).all()

        return [
            {
                "decision_id": decision.id,
                "portfolio_id": decision.portfolio_id,
                "symbol": asset.symbol,
                "action": decision.action,
                "quantity": float(decision.quantity),
                "reference_price_usd": float(
                    decision.reference_price_usd
                ),
                "confidence_percent": float(
                    decision.confidence_percent
                ),
                "rationale": decision.rationale,
                "policy_name": decision.policy_name,
                "strategy_name": decision.strategy_name,
                "approved": decision.approved,
                "rejection_reasons": (
                    decision.rejection_reasons
                ),
                "execution_status": (
                    decision.execution_status
                ),
                "execution_attempted_at": (
                    decision.execution_attempted_at.isoformat()
                    if decision.execution_attempted_at is not None
                    else None
                ),
                "executed_at": (
                    decision.executed_at.isoformat()
                    if decision.executed_at is not None
                    else None
                ),
                "portfolio_transaction_id": (
                    decision.portfolio_transaction_id
                ),
                "execution_error": (
                    decision.execution_error
                ),
                "price_observed_at": (
                    decision.price_observed_at.isoformat()
                ),
                "created_at": (
                    decision.created_at.isoformat()
                ),
                "evaluated_at": (
                    decision.evaluated_at.isoformat()
                ),
            }
            for decision, asset in rows
        ]
