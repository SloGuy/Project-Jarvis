from collections import Counter, defaultdict
from typing import Any

from app.autonomous_trading.journal_queries import (
    get_trade_journal,
)


def _safe_average(
    values: list[float],
) -> float | None:
    if not values:
        return None

    return sum(values) / len(values)


def get_journal_analytics(
    *,
    limit: int = 1000,
    portfolio_id: int | None = None,
    strategy_name: str | None = None,
) -> dict[str, Any]:
    journal_result = get_trade_journal(
        limit=limit,
        portfolio_id=portfolio_id,
        strategy_name=strategy_name,
    )

    journals = journal_result.get(
        "journals",
        [],
    )

    open_trades = [
        journal
        for journal in journals
        if journal.get("status") == "open"
    ]

    closed_trades = [
        journal
        for journal in journals
        if journal.get("status") == "closed"
    ]

    profitable_trades = [
        journal
        for journal in closed_trades
        if journal.get("actual_outcome")
        == "profitable"
    ]

    unprofitable_trades = [
        journal
        for journal in closed_trades
        if journal.get("actual_outcome")
        == "unprofitable"
    ]

    breakeven_trades = [
        journal
        for journal in closed_trades
        if journal.get("actual_outcome")
        == "breakeven"
    ]

    closed_count = len(closed_trades)

    win_rate_percent = (
        len(profitable_trades)
        / closed_count
        * 100
        if closed_count
        else None
    )

    realized_values = [
        float(
            journal["realized_gain_loss_usd"]
        )
        for journal in closed_trades
        if journal.get(
            "realized_gain_loss_usd"
        )
        is not None
    ]

    return_values = [
        float(journal["return_percent"])
        for journal in closed_trades
        if journal.get("return_percent")
        is not None
    ]

    holding_durations = [
        float(
            journal[
                "holding_duration_seconds"
            ]
        )
        for journal in closed_trades
        if journal.get(
            "holding_duration_seconds"
        )
        is not None
    ]

    thesis_correct_count = sum(
        1
        for journal in closed_trades
        if journal.get("thesis_correct")
        is True
    )

    thesis_failed_count = sum(
        1
        for journal in closed_trades
        if journal.get("thesis_correct")
        is False
    )

    thesis_inconclusive_count = sum(
        1
        for journal in closed_trades
        if journal.get("thesis_correct")
        is None
    )

    learning_classification_counts = Counter(
        journal.get(
            "learning_classification",
            "unknown",
        )
        for journal in journals
    )

    exit_rule_counts = Counter(
        journal.get("exit_rule")
        or "none"
        for journal in closed_trades
    )

    symbol_groups: dict[
        str,
        list[dict[str, Any]],
    ] = defaultdict(list)

    for journal in closed_trades:
        symbol = str(
            journal.get("symbol")
            or "UNKNOWN"
        )

        symbol_groups[symbol].append(
            journal
        )

    performance_by_symbol = []

    for symbol in sorted(symbol_groups):
        symbol_trades = symbol_groups[
            symbol
        ]

        symbol_wins = sum(
            1
            for journal in symbol_trades
            if journal.get("actual_outcome")
            == "profitable"
        )

        symbol_returns = [
            float(
                journal["return_percent"]
            )
            for journal in symbol_trades
            if journal.get(
                "return_percent"
            )
            is not None
        ]

        symbol_realized = [
            float(
                journal[
                    "realized_gain_loss_usd"
                ]
            )
            for journal in symbol_trades
            if journal.get(
                "realized_gain_loss_usd"
            )
            is not None
        ]

        trade_count = len(symbol_trades)

        performance_by_symbol.append(
            {
                "symbol": symbol,
                "trade_count": trade_count,
                "win_count": symbol_wins,
                "loss_count": (
                    trade_count
                    - symbol_wins
                    - sum(
                        1
                        for journal
                        in symbol_trades
                        if journal.get(
                            "actual_outcome"
                        )
                        == "breakeven"
                    )
                ),
                "win_rate_percent": (
                    symbol_wins
                    / trade_count
                    * 100
                    if trade_count
                    else None
                ),
                "realized_gain_loss_usd": (
                    sum(symbol_realized)
                ),
                "average_return_percent": (
                    _safe_average(
                        symbol_returns
                    )
                ),
            }
        )

    exit_rule_groups: dict[
        str,
        list[dict[str, Any]],
    ] = defaultdict(list)

    for journal in closed_trades:
        exit_rule = str(
            journal.get("exit_rule")
            or "none"
        )

        exit_rule_groups[
            exit_rule
        ].append(journal)

    performance_by_exit_rule = []

    for exit_rule in sorted(
        exit_rule_groups
    ):
        rule_trades = exit_rule_groups[
            exit_rule
        ]

        rule_returns = [
            float(
                journal["return_percent"]
            )
            for journal in rule_trades
            if journal.get(
                "return_percent"
            )
            is not None
        ]

        rule_realized = [
            float(
                journal[
                    "realized_gain_loss_usd"
                ]
            )
            for journal in rule_trades
            if journal.get(
                "realized_gain_loss_usd"
            )
            is not None
        ]

        rule_wins = sum(
            1
            for journal in rule_trades
            if journal.get("actual_outcome")
            == "profitable"
        )

        performance_by_exit_rule.append(
            {
                "exit_rule": exit_rule,
                "trade_count": len(
                    rule_trades
                ),
                "win_count": rule_wins,
                "realized_gain_loss_usd": (
                    sum(rule_realized)
                ),
                "average_return_percent": (
                    _safe_average(
                        rule_returns
                    )
                ),
            }
        )

    return {
        "status": "success",
        "total_trade_count": len(
            journals
        ),
        "open_trade_count": len(
            open_trades
        ),
        "closed_trade_count": (
            closed_count
        ),
        "win_count": len(
            profitable_trades
        ),
        "loss_count": len(
            unprofitable_trades
        ),
        "breakeven_count": len(
            breakeven_trades
        ),
        "win_rate_percent": (
            win_rate_percent
        ),
        "cumulative_realized_gain_loss_usd": (
            sum(realized_values)
        ),
        "average_return_percent": (
            _safe_average(
                return_values
            )
        ),
        "average_holding_duration_seconds": (
            _safe_average(
                holding_durations
            )
        ),
        "thesis_correct_count": (
            thesis_correct_count
        ),
        "thesis_failed_count": (
            thesis_failed_count
        ),
        "thesis_inconclusive_count": (
            thesis_inconclusive_count
        ),
        "learning_classification_counts": (
            dict(
                learning_classification_counts
            )
        ),
        "exit_rule_counts": dict(
            exit_rule_counts
        ),
        "performance_by_symbol": (
            performance_by_symbol
        ),
        "performance_by_exit_rule": (
            performance_by_exit_rule
        ),
    }


def summarize_journal_analytics(
    analytics: dict[str, Any] | None = None,
) -> str:
    if analytics is None:
        analytics = get_journal_analytics()

    total_trade_count = int(
        analytics.get("total_trade_count")
        or 0
    )

    open_trade_count = int(
        analytics.get("open_trade_count")
        or 0
    )

    closed_trade_count = int(
        analytics.get("closed_trade_count")
        or 0
    )

    win_count = int(
        analytics.get("win_count")
        or 0
    )

    win_rate_percent = analytics.get(
        "win_rate_percent"
    )

    cumulative_realized = float(
        analytics.get(
            "cumulative_realized_gain_loss_usd"
        )
        or 0
    )

    average_return = analytics.get(
        "average_return_percent"
    )

    thesis_failed_count = int(
        analytics.get("thesis_failed_count")
        or 0
    )

    performance_by_symbol = analytics.get(
        "performance_by_symbol"
    ) or []

    performance_by_exit_rule = analytics.get(
        "performance_by_exit_rule"
    ) or []

    parts = [
        (
            f"Jarvis has recorded "
            f"{total_trade_count} autonomous trade "
            f"lifecycle"
            f"{'' if total_trade_count == 1 else 's'}, "
            f"with {open_trade_count} still open and "
            f"{closed_trade_count} completed."
        )
    ]

    if closed_trade_count == 0:
        parts.append(
            "No trades have closed yet, so realized "
            "performance and thesis accuracy cannot "
            "be evaluated."
        )

        return " ".join(parts)

    win_rate_text = (
        f"{float(win_rate_percent):.1f}%"
        if win_rate_percent is not None
        else "unavailable"
    )

    average_return_text = (
        f"{float(average_return):+.2f}%"
        if average_return is not None
        else "unavailable"
    )

    cumulative_realized_text = (
        f"-${abs(cumulative_realized):,.2f}"
        if cumulative_realized < 0
        else f"${cumulative_realized:,.2f}"
    )

    parts.append(
        (
            f"The completed trades have produced "
            f"a cumulative realized P/L of "
            f"{cumulative_realized_text}, "
            f"an average return of "
            f"{average_return_text}, and a "
            f"{win_rate_text} win rate "
            f"({win_count} win"
            f"{'' if win_count == 1 else 's'})."
        )
    )

    if (
        len(performance_by_symbol) == 1
        and closed_trade_count > 0
    ):
        symbol = performance_by_symbol[0].get(
            "symbol",
            "UNKNOWN",
        )

        parts.append(
            (
                f"All completed trades so far "
                f"have been in {symbol}."
            )
        )

    if (
        len(performance_by_exit_rule) == 1
        and closed_trade_count > 0
    ):
        exit_rule = str(
            performance_by_exit_rule[0].get(
                "exit_rule",
                "unknown",
            )
        ).replace("_", " ")

        parts.append(
            (
                f"All completed trades exited through "
                f"the {exit_rule} rule."
            )
        )

    if thesis_failed_count:
        parts.append(
            (
                f"{thesis_failed_count} completed "
                f"trade"
                f"{'' if thesis_failed_count == 1 else 's'} "
                f"were classified as thesis failures."
            )
        )

    if closed_trade_count < 20:
        parts.append(
            (
                "The completed-trade sample is still "
                "small, so these results should be "
                "treated as early evidence rather "
                "than a reliable strategy conclusion."
            )
        )

    return " ".join(parts)
