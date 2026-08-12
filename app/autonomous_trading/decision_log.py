from sqlalchemy import select

from app.autonomous_trading.proposals import TradeProposal
from app.autonomous_trading.risk_governor import RiskDecision
from app.market_db.database import SessionLocal
from app.market_db.models import (
    AutonomousTradeDecision,
    MarketAsset,
    Portfolio,
)


class DecisionLogError(ValueError):
    """Raised when an autonomous trade decision cannot be logged."""


def log_trade_decision(
    *,
    proposal: TradeProposal,
    decision: RiskDecision,
    portfolio_id: int,
) -> AutonomousTradeDecision:
    """
    Persist an autonomous trade proposal and its risk decision.

    This function records decisions only.
    It does not execute trades.
    """

    with SessionLocal() as session:
        portfolio = session.scalar(
            select(Portfolio).where(
                Portfolio.id == portfolio_id,
                Portfolio.is_active.is_(True),
            )
        )

        if portfolio is None:
            raise DecisionLogError(
                f"Active portfolio {portfolio_id} was not found."
            )

        asset = session.scalar(
            select(MarketAsset)
            .where(
                MarketAsset.symbol == proposal.symbol,
                MarketAsset.is_active.is_(True),
            )
            .order_by(MarketAsset.id)
            .limit(1)
        )

        if asset is None:
            raise DecisionLogError(
                f"Active asset {proposal.symbol} was not found."
            )

        record = AutonomousTradeDecision(
            portfolio_id=portfolio.id,
            asset_id=asset.id,
            policy_name=decision.policy_name,
            strategy_name=proposal.strategy_name,
            action=proposal.action.value,
            quantity=proposal.quantity,
            reference_price_usd=proposal.reference_price_usd,
            price_observed_at=proposal.price_observed_at,
            confidence_percent=proposal.confidence_percent,
            rationale=proposal.rationale,
            approved=decision.approved,
            rejection_reasons=list(decision.reasons),
            created_at=proposal.created_at,
            evaluated_at=decision.evaluated_at,
        )

        session.add(record)
        session.commit()
        session.refresh(record)

        return record
