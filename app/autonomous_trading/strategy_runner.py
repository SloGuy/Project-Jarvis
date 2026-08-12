from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.autonomous_trading.candidate_to_proposal import (
    candidate_to_trade_proposal,
)
from app.autonomous_trading.decision_log import (
    log_trade_decision,
)
from app.autonomous_trading.execution_gate import (
    execute_approved_proposal,
)
from app.autonomous_trading.execution_state import (
    claim_decision_for_execution,
    mark_execution_complete,
    mark_execution_failed,
)
from app.autonomous_trading.momentum_strategy import (
    evaluate_momentum_strategy,
)
from app.autonomous_trading.policy import (
    INITIAL_1000_POLICY,
)
from app.autonomous_trading.risk_governor import (
    evaluate_trade_proposal,
)
from app.autonomous_trading.strategy import (
    PositionContext,
    StrategyAction,
)
from app.market_db.database import SessionLocal
from app.market_db.models import MarketAsset
from app.market_db.portfolio_queries import (
    get_portfolio_summary,
)


def run_momentum_strategy_cycle() -> dict[str, Any]:
    """
    Evaluate the momentum strategy across all active assets.

    Confirmed BUY/SELL candidates are converted into proposals,
    evaluated by the risk governor, and persisted in the
    autonomous decision log.

    Execution is attempted only when the active policy explicitly
    enables autonomous execution.

    No execution occurs while
    INITIAL_1000_POLICY.autonomous_execution_enabled is False.
    """

    portfolio = get_portfolio_summary()

    if portfolio.get("status") != "success":
        raise RuntimeError(
            "Portfolio state is unavailable."
        )

    portfolio_id = portfolio["portfolio"]["id"]

    positions_by_symbol = {
        str(position["symbol"]).upper(): position
        for position in portfolio.get("positions", [])
    }

    with SessionLocal() as session:
        symbols = session.scalars(
            select(MarketAsset.symbol)
            .where(
                MarketAsset.is_active.is_(True),
            )
            .order_by(MarketAsset.symbol)
        ).all()

    results: list[dict[str, Any]] = []

    for symbol in symbols:
        normalized_symbol = str(symbol).upper()

        position = positions_by_symbol.get(
            normalized_symbol
        )

        if position is None:
            position_context = PositionContext(
                symbol=normalized_symbol,
                quantity=Decimal("0"),
                average_cost_usd=Decimal("0"),
                market_value_usd=Decimal("0"),
                allocation_percent=Decimal("0"),
                unrealized_gain_loss_usd=Decimal("0"),
                unrealized_gain_loss_percent=Decimal("0"),
            )
        else:
            position_context = PositionContext(
                symbol=normalized_symbol,
                quantity=Decimal(
                    str(position.get("quantity") or 0)
                ),
                average_cost_usd=Decimal(
                    str(position.get("average_cost_usd") or 0)
                ),
                market_value_usd=Decimal(
                    str(position.get("market_value_usd") or 0)
                ),
                allocation_percent=Decimal(
                    str(position.get("allocation_percent") or 0)
                ),
                unrealized_gain_loss_usd=Decimal(
                    str(
                        position.get(
                            "unrealized_gain_loss_usd"
                        )
                        or 0
                    )
                ),
                unrealized_gain_loss_percent=Decimal(
                    str(
                        position.get(
                            "unrealized_gain_loss_percent"
                        )
                        or 0
                    )
                ),
            )

        candidate = evaluate_momentum_strategy(
            symbol=normalized_symbol,
            position_context=position_context,
        )

        result: dict[str, Any] = {
            "symbol": candidate.symbol,
            "strategy_action": candidate.action.value,
            "confidence_percent": float(
                candidate.confidence_percent
            ),
            "rationale": candidate.rationale,
            "has_position": position_context.has_position,
            "position_quantity": float(
                position_context.quantity
            ),
            "position_average_cost_usd": float(
                position_context.average_cost_usd
            ),
            "position_market_value_usd": float(
                position_context.market_value_usd
            ),
            "position_allocation_percent": float(
                position_context.allocation_percent
            ),
            "position_unrealized_gain_loss_percent": float(
                position_context.unrealized_gain_loss_percent
            ),
            "proposal_created": False,
            "decision_logged": False,
            "risk_approved": None,
            "decision_id": None,
            "execution_attempted": False,
            "execution_status": None,
            "execution_reason": None,
            "transaction_id": None,
        }

        if candidate.action == StrategyAction.HOLD:
            results.append(result)
            continue

        proposal = candidate_to_trade_proposal(
            candidate=candidate,
        )

        if proposal is None:
            results.append(result)
            continue

        result["proposal_created"] = True

        decision = evaluate_trade_proposal(
            proposal=proposal,
            policy=INITIAL_1000_POLICY,
            portfolio_summary=portfolio,
        )

        record = log_trade_decision(
            proposal=proposal,
            decision=decision,
            portfolio_id=portfolio_id,
        )

        result["decision_logged"] = True
        result["risk_approved"] = decision.approved
        result["decision_id"] = record.id

        if not decision.approved:
            result["execution_status"] = "not_executed"
            result["execution_reason"] = (
                "Risk governor rejected the proposal."
            )
            results.append(result)
            continue

        if not INITIAL_1000_POLICY.autonomous_execution_enabled:
            result["execution_status"] = "not_executed"
            result["execution_reason"] = (
                "Autonomous execution is disabled "
                "by the active risk policy."
            )
            results.append(result)
            continue

        claim = claim_decision_for_execution(
            decision_id=record.id,
        )

        if not claim.claimed:
            result["execution_status"] = (
                claim.execution_status
            )
            result["execution_reason"] = claim.reason
            results.append(result)
            continue

        result["execution_attempted"] = True

        try:
            execution = execute_approved_proposal(
                decision_id=record.id,
                proposal=proposal,
                decision=decision,
                policy=INITIAL_1000_POLICY,
                portfolio_id=portfolio_id,
            )

            if not execution.executed:
                mark_execution_failed(
                    decision_id=record.id,
                    error_message=execution.reason,
                )

                result["execution_status"] = "failed"
                result["execution_reason"] = execution.reason
                results.append(result)
                continue

            transaction = execution.transaction

            transaction_id = execution.transaction_id

            if transaction_id is None:
                raise RuntimeError(
                    "Execution returned no transaction ID."
                )

            mark_execution_complete(
                decision_id=record.id,
                portfolio_transaction_id=transaction_id,
            )

            result["execution_status"] = "executed"
            result["execution_reason"] = (
                execution.reason
            )
            result["transaction_id"] = transaction_id

        except Exception as exc:
            mark_execution_failed(
                decision_id=record.id,
                error_message=str(exc),
            )

            result["execution_status"] = "failed"
            result["execution_reason"] = str(exc)

        results.append(result)

    return {
        "status": "success",
        "strategy": "momentum_alignment_v1",
        "asset_count": len(symbols),
        "actionable_count": sum(
            1
            for result in results
            if result["proposal_created"]
        ),
        "approved_count": sum(
            1
            for result in results
            if result["risk_approved"] is True
        ),
        "rejected_count": sum(
            1
            for result in results
            if result["risk_approved"] is False
        ),
        "execution_attempted_count": sum(
            1
            for result in results
            if result["execution_attempted"]
        ),
        "executed_count": sum(
            1
            for result in results
            if result["execution_status"] == "executed"
        ),
        "execution_failed_count": sum(
            1
            for result in results
            if result["execution_status"] == "failed"
        ),
        "results": results,
    }
