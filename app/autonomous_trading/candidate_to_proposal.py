from decimal import Decimal

from app.autonomous_trading.proposal_builder import (
    build_trade_proposal,
)
from app.autonomous_trading.proposals import TradeAction, TradeProposal
from app.autonomous_trading.strategy import (
    StrategyAction,
    StrategyCandidate,
)
from app.market_db.portfolio_queries import get_portfolio_summary


class CandidateConversionError(ValueError):
    """Raised when a strategy candidate cannot become a trade proposal."""


def candidate_to_trade_proposal(
    *,
    candidate: StrategyCandidate,
    portfolio_id: int | None = None,
) -> TradeProposal | None:
    """
    Convert a strategy candidate into a priced trade proposal.

    HOLD candidates return None.

    This function does not approve or execute trades.
    """

    if candidate.action == StrategyAction.HOLD:
        return None

    portfolio = get_portfolio_summary(
        portfolio_id=portfolio_id,
    )

    if portfolio.get("status") != "success":
        raise CandidateConversionError(
            "Portfolio state is unavailable."
        )

    total_value = Decimal(
        str(portfolio["total_value_usd"])
    )

    if total_value <= Decimal("0"):
        raise CandidateConversionError(
            "Portfolio total value must be greater than zero."
        )

    if candidate.action == StrategyAction.BUY:
        trade_action = TradeAction.BUY

        target_value = (
            total_value
            * candidate.suggested_position_percent
            / Decimal("100")
        )

    elif candidate.action == StrategyAction.SELL:
        trade_action = TradeAction.SELL

        matching_position = next(
            (
                position
                for position in portfolio.get(
                    "positions",
                    []
                )
                if str(
                    position.get("symbol", "")
                ).upper()
                == candidate.symbol.upper()
            ),
            None,
        )

        if matching_position is None:
            raise CandidateConversionError(
                f"No open {candidate.symbol} position "
                f"is available to sell."
            )

        held_quantity = Decimal(
            str(
                matching_position.get("quantity")
                or 0
            )
        )

        if held_quantity <= Decimal("0"):
            raise CandidateConversionError(
                f"No positive {candidate.symbol} "
                f"quantity is available to sell."
            )

    else:
        raise CandidateConversionError(
            "Unsupported strategy action."
        )

    preview = build_trade_proposal(
        symbol=candidate.symbol,
        action=trade_action,
        quantity=Decimal("1"),
        confidence_percent=candidate.confidence_percent,
        rationale=candidate.rationale,
        strategy_name=candidate.strategy_name,
    )

    if candidate.action == StrategyAction.BUY:
        quantity = (
            target_value
            / preview.reference_price_usd
        )
    else:
        quantity = held_quantity

    return build_trade_proposal(
        symbol=candidate.symbol,
        action=trade_action,
        quantity=quantity,
        confidence_percent=candidate.confidence_percent,
        rationale=candidate.rationale,
        strategy_name=candidate.strategy_name,
    )
