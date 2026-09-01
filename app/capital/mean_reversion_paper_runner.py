from decimal import Decimal
from typing import Any

from app.autonomous_trading.exit_rules import (
    evaluate_exit_rules,
)
from app.autonomous_trading.mean_reversion_strategy import (
    STRATEGY_NAME,
    evaluate_mean_reversion_strategy,
    get_mean_reversion_snapshot,
)
from app.autonomous_trading.strategy import (
    StrategyAction,
    create_strategy_candidate,
)
from app.autonomous_trading.trade_journal import (
    close_trade_journal,
    open_trade_journal,
)
from app.capital.candidate_pipeline import (
    process_candidate,
)
from app.capital.mean_reversion_runner import (
    _position_context,
    get_mean_reversion_universe,
)
from app.capital.policies import (
    MEAN_REVERSION_1000_POLICY,
)
from app.capital.portfolio_service import (
    get_or_create_mean_reversion_portfolio,
)
from app.market_db.portfolio_queries import (
    get_portfolio_summary,
)


def _snapshot_context(
    snapshot,
    portfolio: dict[str, Any],
) -> dict[str, Any]:
    return {
        "latest_price_usd": (
            float(snapshot.latest_price_usd)
            if snapshot.latest_price_usd is not None
            else None
        ),
        "mean_price_usd": (
            float(snapshot.mean_price_usd)
            if snapshot.mean_price_usd is not None
            else None
        ),
        "standard_deviation_usd": (
            float(snapshot.standard_deviation_usd)
            if snapshot.standard_deviation_usd is not None
            else None
        ),
        "z_score": (
            float(snapshot.z_score)
            if snapshot.z_score is not None
            else None
        ),
        "observation_count": snapshot.observation_count,
        "portfolio_total_value_usd": float(
            portfolio["total_value_usd"]
        ),
        "cash_balance_usd": float(
            portfolio["cash_balance_usd"]
        ),
    }


def run_mean_reversion_paper_cycle() -> dict[str, Any]:
    portfolio_record = (
        get_or_create_mean_reversion_portfolio()
    )

    portfolio = get_portfolio_summary(
        portfolio_id=portfolio_record.id,
    )

    if portfolio.get("status") != "success":
        raise RuntimeError(
            "Mean-reversion portfolio is unavailable."
        )

    positions_by_symbol = {
        str(position["symbol"]).upper(): position
        for position in portfolio.get("positions", [])
    }

    symbols = get_mean_reversion_universe()
    results: list[dict[str, Any]] = []

    for symbol in symbols:
        normalized_symbol = symbol.upper()

        position_context = _position_context(
            symbol=normalized_symbol,
            position=positions_by_symbol.get(
                normalized_symbol
            ),
        )

        snapshot = get_mean_reversion_snapshot(
            symbol=normalized_symbol,
        )

        risk_exit = evaluate_exit_rules(
            position_context=position_context,
            policy=MEAN_REVERSION_1000_POLICY,
        )

        if risk_exit.should_exit:
            candidate = create_strategy_candidate(
                symbol=normalized_symbol,
                action=StrategyAction.SELL,
                confidence_percent=Decimal("100.00"),
                rationale=risk_exit.rationale,
                suggested_position_percent=Decimal("0"),
                strategy_name="exit_risk_management_v1",
            )
        else:
            candidate = evaluate_mean_reversion_strategy(
                symbol=normalized_symbol,
                position_context=position_context,
                snapshot=snapshot,
            )

        current_portfolio = get_portfolio_summary(
            portfolio_id=portfolio_record.id,
        )

        pipeline = process_candidate(
            candidate=candidate,
            portfolio_id=portfolio_record.id,
            portfolio_summary=current_portfolio,
            policy=MEAN_REVERSION_1000_POLICY,
        )

        exit_rule = None

        if risk_exit.should_exit:
            exit_rule = risk_exit.rule.value
        elif candidate.action == StrategyAction.SELL:
            exit_rule = "mean_recovery"

        if pipeline.execution_status == "executed":
            if (
                pipeline.decision_id is None
                or pipeline.transaction_id is None
            ):
                raise RuntimeError(
                    "Executed candidate is missing identifiers."
                )

            market_context = _snapshot_context(
                snapshot,
                current_portfolio,
            )

            if candidate.action == StrategyAction.BUY:
                open_trade_journal(
                    decision_id=pipeline.decision_id,
                    transaction_id=pipeline.transaction_id,
                    entry_market_context=market_context,
                    expected_outcome=(
                        "Price recovers toward its rolling mean."
                    ),
                )

            if candidate.action == StrategyAction.SELL:
                close_trade_journal(
                    decision_id=pipeline.decision_id,
                    transaction_id=pipeline.transaction_id,
                    exit_rule=exit_rule,
                    exit_market_context=market_context,
                )

        results.append(
            {
                "symbol": normalized_symbol,
                "action": candidate.action.value,
                "confidence_percent": float(
                    candidate.confidence_percent
                ),
                "rationale": candidate.rationale,
                "z_score": (
                    float(snapshot.z_score)
                    if snapshot.z_score is not None
                    else None
                ),
                "exit_rule": exit_rule,
                "proposal_created": (
                    pipeline.proposal_created
                ),
                "decision_logged": (
                    pipeline.decision_logged
                ),
                "risk_approved": (
                    pipeline.risk_approved
                ),
                "risk_reasons": list(
                    pipeline.risk_reasons
                ),
                "decision_id": pipeline.decision_id,
                "execution_attempted": (
                    pipeline.execution_attempted
                ),
                "execution_status": (
                    pipeline.execution_status
                ),
                "execution_reason": (
                    pipeline.execution_reason
                ),
                "transaction_id": (
                    pipeline.transaction_id
                ),
            }
        )

    return {
        "status": "success",
        "strategy": STRATEGY_NAME,
        "policy": MEAN_REVERSION_1000_POLICY.name,
        "portfolio_id": portfolio_record.id,
        "autonomous_execution_enabled": (
            MEAN_REVERSION_1000_POLICY
            .autonomous_execution_enabled
        ),
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
