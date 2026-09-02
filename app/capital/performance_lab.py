from typing import Any

from app.autonomous_trading.journal_analytics import (
    get_journal_analytics,
)
from app.autonomous_trading.journal_queries import (
    get_trade_journal,
)
from app.capital.portfolio_status import (
    get_strategy_portfolios,
)


def _maximum_drawdown_percent(
    *,
    starting_capital_usd: float,
    journals: list[dict[str, Any]],
) -> float:
    equity = starting_capital_usd
    peak = starting_capital_usd
    maximum_drawdown = 0.0

    closed_journals = sorted(
        (
            journal
            for journal in journals
            if journal.get("status") == "closed"
        ),
        key=lambda journal: (
            journal.get("closed_at") or ""
        ),
    )

    for journal in closed_journals:
        equity += float(
            journal.get("realized_gain_loss_usd")
            or 0
        )

        peak = max(peak, equity)

        if peak > 0:
            drawdown = (
                (peak - equity)
                / peak
                * 100
            )

            maximum_drawdown = max(
                maximum_drawdown,
                drawdown,
            )

    return maximum_drawdown


def _sample_maturity(
    closed_trade_count: int,
) -> str:
    if closed_trade_count < 30:
        return "insufficient"

    if closed_trade_count < 100:
        return "developing"

    return "substantial"


def get_performance_lab() -> dict[str, Any]:
    results: list[dict[str, Any]] = []

    for portfolio in get_strategy_portfolios():
        if portfolio.get("portfolio_status") != "success":
            continue

        portfolio_id = int(
            portfolio["portfolio_id"]
        )

        analytics = get_journal_analytics(
            portfolio_id=portfolio_id,
        )

        journal_result = get_trade_journal(
            portfolio_id=portfolio_id,
            limit=10000,
        )

        journals = journal_result["journals"]

        closed_journals = [
            journal
            for journal in journals
            if journal.get("status") == "closed"
        ]

        realized_results = [
            float(
                journal.get(
                    "realized_gain_loss_usd"
                )
                or 0
            )
            for journal in closed_journals
        ]

        gross_profit = sum(
            result
            for result in realized_results
            if result > 0
        )

        gross_loss = abs(
            sum(
                result
                for result in realized_results
                if result < 0
            )
        )

        closed_count = len(closed_journals)

        expectancy_usd = (
            sum(realized_results) / closed_count
            if closed_count
            else None
        )

        profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 0
            else None
        )

        thesis_evaluated = (
            analytics["thesis_correct_count"]
            + analytics["thesis_failed_count"]
        )

        thesis_accuracy = (
            analytics["thesis_correct_count"]
            / thesis_evaluated
            * 100
            if thesis_evaluated
            else None
        )

        current_value = float(
            portfolio["current_value_usd"]
        )

        market_value = float(
            portfolio["market_value_usd"]
        )

        exposure_percent = (
            market_value
            / current_value
            * 100
            if current_value > 0
            else 0.0
        )

        results.append(
            {
                "strategy_name": (
                    portfolio["strategy_name"]
                ),
                "experiment_id": (
                    portfolio["experiment_id"]
                ),
                "portfolio_id": portfolio_id,
                "experiment_status": (
                    portfolio["experiment_status"]
                ),
                "current_value_usd": current_value,
                "total_return_percent": float(
                    portfolio[
                        "total_return_percent"
                    ]
                ),
                "closed_trade_count": closed_count,
                "win_rate_percent": (
                    analytics["win_rate_percent"]
                ),
                "average_return_percent": (
                    analytics[
                        "average_return_percent"
                    ]
                ),
                "expectancy_usd": expectancy_usd,
                "profit_factor": profit_factor,
                "profit_factor_status": (
                    "calculated"
                    if gross_loss > 0
                    else "no_losses"
                ),
                "gross_profit_usd": gross_profit,
                "gross_loss_usd": gross_loss,
                "maximum_drawdown_percent": (
                    _maximum_drawdown_percent(
                        starting_capital_usd=float(
                            portfolio[
                                "starting_capital_usd"
                            ]
                        ),
                        journals=journals,
                    )
                ),
                "thesis_accuracy_percent": (
                    thesis_accuracy
                ),
                "exposure_percent": exposure_percent,
                "open_position_count": int(
                    portfolio[
                        "open_position_count"
                    ]
                ),
                "sample_maturity": (
                    _sample_maturity(closed_count)
                ),
                **_attribution_summary(
                    analytics
                ),
            }
        )

    return {
        "status": "success",
        "strategy_count": len(results),
        "strategies": results,
    }


def _attribution_summary(
    analytics: dict[str, Any],
) -> dict[str, Any]:
    by_symbol = analytics[
        "performance_by_symbol"
    ]

    by_exit_rule = analytics[
        "performance_by_exit_rule"
    ]

    best_symbol = (
        max(
            by_symbol,
            key=lambda item: item[
                "realized_gain_loss_usd"
            ],
        )
        if by_symbol
        else None
    )

    worst_symbol = (
        min(
            by_symbol,
            key=lambda item: item[
                "realized_gain_loss_usd"
            ],
        )
        if by_symbol
        else None
    )

    positive_symbol_profit = sum(
        max(
            0.0,
            float(
                item["realized_gain_loss_usd"]
            ),
        )
        for item in by_symbol
    )

    top_profit_concentration = (
        float(
            best_symbol[
                "realized_gain_loss_usd"
            ]
        )
        / positive_symbol_profit
        * 100
        if (
            best_symbol is not None
            and positive_symbol_profit > 0
        )
        else None
    )

    return {
        "performance_by_symbol": by_symbol,
        "performance_by_exit_rule": by_exit_rule,
        "best_symbol": best_symbol,
        "worst_symbol": worst_symbol,
        "top_profit_concentration_percent": (
            top_profit_concentration
        ),
    }
