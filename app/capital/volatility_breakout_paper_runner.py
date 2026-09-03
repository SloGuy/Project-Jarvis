from decimal import Decimal
from typing import Any

from app.autonomous_trading.exit_rules import (
    evaluate_exit_rules,
)
from app.autonomous_trading.volatility_breakout_strategy import (
    STRATEGY_NAME,
    evaluate_volatility_breakout_strategy,
    get_volatility_breakout_snapshot,
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
from app.capital.volatility_breakout_runner import (
    _position_context,
    get_volatility_breakout_universe,
)
from app.capital.policies import (
    VOLATILITY_BREAKOUT_1000_POLICY,
)
from app.capital.portfolio_service import (
    get_or_create_volatility_breakout_portfolio,
)
from app.market_db.portfolio_queries import (
    get_portfolio_summary,
)


def _snapshot_context(
    snapshot,
    portfolio: dict[str, Any],
) -> dict[str, Any]:
    def optional_float(value):
        return (
            float(value)
            if value is not None
            else None
        )

    return {
        "latest_price_usd": optional_float(
            snapshot.latest_price_usd
        ),
        "compressed_range_high_usd": (
            optional_float(
                snapshot
                .compressed_range_high_usd
            )
        ),
        "compressed_range_low_usd": (
            optional_float(
                snapshot
                .compressed_range_low_usd
            )
        ),
        "compression_ratio": optional_float(
            snapshot.compression_ratio
        ),
        "breakout_percent": optional_float(
            snapshot.breakout_percent
        ),
        "expansion_ratio": optional_float(
            snapshot.expansion_ratio
        ),
        "exit_average_usd": optional_float(
            snapshot.exit_average_usd
        ),
        "observation_count": (
            snapshot.observation_count
        ),
        "portfolio_total_value_usd": float(
            portfolio["total_value_usd"]
        ),
        "cash_balance_usd": float(
            portfolio["cash_balance_usd"]
        ),
    }


def run_volatility_breakout_paper_cycle() -> dict[str, Any]:
    portfolio_record = (
        get_or_create_volatility_breakout_portfolio()
    )

    portfolio = get_portfolio_summary(
        portfolio_id=portfolio_record.id,
    )

    if portfolio.get("status") != "success":
        raise RuntimeError(
            "Volatility-breakout portfolio is unavailable."
        )

    positions_by_symbol = {
        str(position["symbol"]).upper(): position
        for position in portfolio.get("positions", [])
    }

    symbols = get_volatility_breakout_universe()
    results: list[dict[str, Any]] = []

    for symbol in symbols:
        normalized_symbol = symbol.upper()

        position_context = _position_context(
            symbol=normalized_symbol,
            position=positions_by_symbol.get(
                normalized_symbol
            ),
        )

        snapshot = get_volatility_breakout_snapshot(
            symbol=normalized_symbol,
        )

        risk_exit = evaluate_exit_rules(
            position_context=position_context,
            policy=VOLATILITY_BREAKOUT_1000_POLICY,
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
            candidate = evaluate_volatility_breakout_strategy(
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
            policy=VOLATILITY_BREAKOUT_1000_POLICY,
        )

        exit_rule = None

        if risk_exit.should_exit:
            exit_rule = risk_exit.rule.value
        elif candidate.action == StrategyAction.SELL:
            exit_rule = "breakout_failure"

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
                "compression_ratio": (
                    float(snapshot.compression_ratio)
                    if snapshot.compression_ratio
                    is not None
                    else None
                ),
                "breakout_percent": (
                    float(snapshot.breakout_percent)
                    if snapshot.breakout_percent
                    is not None
                    else None
                ),
                "expansion_ratio": (
                    float(snapshot.expansion_ratio)
                    if snapshot.expansion_ratio
                    is not None
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
        "policy": VOLATILITY_BREAKOUT_1000_POLICY.name,
        "portfolio_id": portfolio_record.id,
        "autonomous_execution_enabled": (
            VOLATILITY_BREAKOUT_1000_POLICY
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
