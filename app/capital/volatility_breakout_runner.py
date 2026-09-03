from typing import Any

from app.autonomous_trading.strategy import (
    StrategyAction,
)
from app.autonomous_trading.volatility_breakout_strategy import (
    STRATEGY_NAME,
    evaluate_volatility_breakout_strategy,
    get_volatility_breakout_snapshot,
)
from app.autonomous_trading.candidate_to_proposal import (
    candidate_to_trade_proposal,
)
from app.autonomous_trading.risk_governor import (
    evaluate_trade_proposal,
)
from app.capital.mean_reversion_runner import (
    _position_context,
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


VOLATILITY_BREAKOUT_UNIVERSE = (
    "SPY",
    "QQQ",
    "DIA",
    "AAPL",
    "TSLA",
    "NVDA",
    "BTC",
    "ETH",
)


def get_volatility_breakout_universe() -> tuple[str, ...]:
    return VOLATILITY_BREAKOUT_UNIVERSE


def run_volatility_breakout_dry_cycle() -> dict[str, Any]:
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
    results = []

    for symbol in symbols:
        position_context = _position_context(
            symbol=symbol,
            position=positions_by_symbol.get(symbol),
        )

        snapshot = get_volatility_breakout_snapshot(
            symbol=symbol,
        )

        candidate = (
            evaluate_volatility_breakout_strategy(
                symbol=symbol,
                position_context=position_context,
                snapshot=snapshot,
            )
        )

        proposal_created = False
        risk_approved = None
        risk_reasons = []

        if candidate.action != StrategyAction.HOLD:
            proposal = candidate_to_trade_proposal(
                candidate=candidate,
                portfolio_id=portfolio_record.id,
            )

            if proposal is not None:
                decision = evaluate_trade_proposal(
                    proposal=proposal,
                    policy=(
                        VOLATILITY_BREAKOUT_1000_POLICY
                    ),
                    portfolio_summary=portfolio,
                )

                proposal_created = True
                risk_approved = decision.approved
                risk_reasons = list(
                    decision.reasons
                )

        results.append(
            {
                "symbol": symbol,
                "action": candidate.action.value,
                "confidence_percent": float(
                    candidate.confidence_percent
                ),
                "rationale": candidate.rationale,
                "snapshot_usable": snapshot.usable,
                "observation_count": (
                    snapshot.observation_count
                ),
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
                "proposal_created": proposal_created,
                "risk_approved": risk_approved,
                "risk_reasons": risk_reasons,
                "execution_attempted": False,
            }
        )

    return {
        "status": "success",
        "mode": "dry_run",
        "strategy": STRATEGY_NAME,
        "policy": (
            VOLATILITY_BREAKOUT_1000_POLICY.name
        ),
        "portfolio_id": portfolio_record.id,
        "autonomous_execution_enabled": (
            VOLATILITY_BREAKOUT_1000_POLICY
            .autonomous_execution_enabled
        ),
        "asset_count": len(symbols),
        "usable_snapshot_count": sum(
            1
            for result in results
            if result["snapshot_usable"]
        ),
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
        "execution_attempted_count": sum(
            1
            for result in results
            if result["execution_attempted"]
        ),
        "results": results,
    }
