from datetime import datetime, timezone
from typing import Any

from app.market_db.portfolio_queries import (
    get_portfolio_summary,
)


def _format_percent(value: float | None) -> str:
    if value is None:
        return "unknown"

    return f"{value:.2f}%"


def _format_money(value: float | None) -> str:
    if value is None:
        return "unavailable"

    return f"${value:,.2f}"


def _determine_risk_level(
    invested_allocation_percent: float | None,
) -> str:
    if invested_allocation_percent is None:
        return "unknown"

    if invested_allocation_percent < 10:
        return "low"

    if invested_allocation_percent < 40:
        return "moderate"

    if invested_allocation_percent < 75:
        return "elevated"

    return "high"


def _calculate_confidence(
    portfolio_data: dict[str, Any],
) -> tuple[int, list[str]]:
    score = 100
    reasons: list[str] = []
    positions = portfolio_data.get("positions", [])
    now = datetime.now(timezone.utc)

    if portfolio_data.get("generated_at"):
        reasons.append(
            "Portfolio accounting was generated successfully."
        )
    else:
        score -= 20
        reasons.append(
            "Portfolio generation time is unavailable."
        )

    missing_prices = []
    stale_prices = []
    aging_prices = []
    fresh_prices = []

    for position in positions:
        symbol = position.get("symbol", "Unknown")
        latest_price = position.get("latest_price_usd")
        observed_at = position.get("price_observed_at")

        if latest_price is None or not observed_at:
            missing_prices.append(symbol)
            continue

        try:
            price_time = datetime.fromisoformat(
                observed_at.replace("Z", "+00:00")
            )

            if price_time.tzinfo is None:
                price_time = price_time.replace(
                    tzinfo=timezone.utc
                )

            age_seconds = (
                now - price_time.astimezone(timezone.utc)
            ).total_seconds()

            if age_seconds <= 30 * 60:
                fresh_prices.append(symbol)
            elif age_seconds <= 2 * 60 * 60:
                aging_prices.append(symbol)
            else:
                stale_prices.append(symbol)

        except (TypeError, ValueError):
            missing_prices.append(symbol)

    if missing_prices:
        penalty = min(45, len(missing_prices) * 20)
        score -= penalty

        reasons.append(
            "Current pricing is unavailable for "
            + ", ".join(missing_prices)
            + "."
        )

    if stale_prices:
        penalty = min(35, len(stale_prices) * 15)
        score -= penalty

        reasons.append(
            "Stored prices are more than two hours old for "
            + ", ".join(stale_prices)
            + "."
        )

    if aging_prices:
        penalty = min(15, len(aging_prices) * 5)
        score -= penalty

        reasons.append(
            "Stored prices are between 30 minutes and two hours "
            "old for "
            + ", ".join(aging_prices)
            + "."
        )

    if positions and len(fresh_prices) == len(positions):
        reasons.append(
            "All open positions have prices observed within "
            "the last 30 minutes."
        )
    elif not positions:
        score -= 5

        reasons.append(
            "Confidence is slightly limited because there are "
            "no open positions to evaluate."
        )

    valuation_fields = (
        "cash_balance_usd",
        "market_value_usd",
        "total_value_usd",
        "unrealized_gain_loss_usd",
        "realized_gain_loss_usd",
        "total_gain_loss_usd",
    )

    missing_valuation_fields = [
        field
        for field in valuation_fields
        if portfolio_data.get(field) is None
    ]

    if missing_valuation_fields:
        score -= min(
            30,
            len(missing_valuation_fields) * 5,
        )

        reasons.append(
            "Some portfolio valuation fields are unavailable."
        )
    else:
        reasons.append(
            "Cash, market value, and gain/loss totals are "
            "complete."
        )

    return max(0, min(score, 100)), reasons


def get_portfolio_insight(
    portfolio_id: int | None = None,
) -> dict[str, Any]:
    portfolio_data = get_portfolio_summary(
        portfolio_id=portfolio_id,
        transaction_limit=10,
    )

    if portfolio_data.get("status") != "success":
        return {
            "status": portfolio_data.get(
                "status",
                "unavailable",
            ),
            "portfolio_id": portfolio_id,
            "summary": portfolio_data.get(
                "summary",
                "Portfolio insight is unavailable.",
            ),
            "executive_summary": (
                "Jarvis could not generate a portfolio assessment."
            ),
            "risk_level": "unknown",
            "confidence_percent": 0,
            "confidence_reasons": [],
            "key_observations": [],
        }

    positions = portfolio_data.get("positions", [])

    cash_allocation = portfolio_data.get(
        "cash_allocation_percent"
    )
    invested_allocation = portfolio_data.get(
        "invested_allocation_percent"
    )
    unrealized_gain_loss = portfolio_data.get(
        "unrealized_gain_loss_usd"
    )
    realized_gain_loss = portfolio_data.get(
        "realized_gain_loss_usd"
    )
    total_gain_loss = portfolio_data.get(
        "total_gain_loss_usd"
    )

    risk_level = _determine_risk_level(
        invested_allocation
    )

    confidence_percent, confidence_reasons = (
        _calculate_confidence(portfolio_data)
    )

    position_count = len(positions)
    key_observations: list[dict[str, str]] = []

    if invested_allocation is not None:
        if invested_allocation < 10:
            key_observations.append(
                {
                    "type": "positive",
                    "title": "Low market exposure",
                    "detail": (
                        f"Only {_format_percent(invested_allocation)} "
                        "of total portfolio value is currently invested."
                    ),
                }
            )
        elif invested_allocation >= 75:
            key_observations.append(
                {
                    "type": "warning",
                    "title": "High market exposure",
                    "detail": (
                        f"{_format_percent(invested_allocation)} "
                        "of total portfolio value is exposed to "
                        "market price movement."
                    ),
                }
            )

    if position_count == 0:
        key_observations.append(
            {
                "type": "neutral",
                "title": "No open positions",
                "detail": (
                    "The portfolio currently has no direct "
                    "market exposure."
                ),
            }
        )
    elif position_count == 1:
        symbol = positions[0].get("symbol", "the asset")

        key_observations.append(
            {
                "type": "warning",
                "title": "Single-asset concentration",
                "detail": (
                    f"All invested capital is concentrated in "
                    f"{symbol}, so the invested portion depends "
                    "entirely on one asset."
                ),
            }
        )
    else:
        largest_position = max(
            positions,
            key=lambda position: (
                position.get("allocation_percent") or 0
            ),
        )

        largest_symbol = largest_position.get(
            "symbol",
            "The largest holding",
        )
        largest_allocation = largest_position.get(
            "allocation_percent"
        )

        key_observations.append(
            {
                "type": "neutral",
                "title": "Largest holding",
                "detail": (
                    f"{largest_symbol} represents "
                    f"{_format_percent(largest_allocation)} "
                    "of total portfolio value."
                ),
            }
        )

    winning_positions = [
        position
        for position in positions
        if (
            position.get("unrealized_gain_loss_usd")
            is not None
            and position["unrealized_gain_loss_usd"] > 0
        )
    ]

    losing_positions = [
        position
        for position in positions
        if (
            position.get("unrealized_gain_loss_usd")
            is not None
            and position["unrealized_gain_loss_usd"] < 0
        )
    ]

    for position in winning_positions[:3]:
        symbol = position.get("symbol", "Unknown")
        latest_price = position.get("latest_price_usd")
        average_cost = position.get("average_cost_usd")
        gain_loss = position.get(
            "unrealized_gain_loss_usd"
        )

        key_observations.append(
            {
                "type": "positive",
                "title": f"{symbol} is above cost basis",
                "detail": (
                    f"{symbol} is trading at "
                    f"{_format_money(latest_price)}, above the "
                    f"average cost of "
                    f"{_format_money(average_cost)}, resulting in "
                    f"an unrealized gain of "
                    f"{_format_money(gain_loss)}."
                ),
            }
        )

    for position in losing_positions[:3]:
        symbol = position.get("symbol", "Unknown")
        latest_price = position.get("latest_price_usd")
        average_cost = position.get("average_cost_usd")
        gain_loss = position.get(
            "unrealized_gain_loss_usd"
        )

        key_observations.append(
            {
                "type": "negative",
                "title": f"{symbol} is below cost basis",
                "detail": (
                    f"{symbol} is trading at "
                    f"{_format_money(latest_price)}, below the "
                    f"average cost of "
                    f"{_format_money(average_cost)}, resulting in "
                    f"an unrealized loss of "
                    f"{_format_money(abs(gain_loss))}."
                ),
            }
        )

    if realized_gain_loss is not None:
        if realized_gain_loss > 0:
            key_observations.append(
                {
                    "type": "positive",
                    "title": "Positive realized performance",
                    "detail": (
                        "Closed paper trades have produced a net "
                        f"realized gain of "
                        f"{_format_money(realized_gain_loss)}."
                    ),
                }
            )
        elif realized_gain_loss < 0:
            key_observations.append(
                {
                    "type": "negative",
                    "title": "Negative realized performance",
                    "detail": (
                        "Closed paper trades have produced a net "
                        f"realized loss of "
                        f"{_format_money(abs(realized_gain_loss))}."
                    ),
                }
            )

    if cash_allocation is None:
        cash_sentence = (
            "The portfolio cash allocation is unavailable."
        )
    elif cash_allocation >= 90:
        cash_sentence = (
            "The portfolio is positioned defensively, with "
            f"{_format_percent(cash_allocation)} of total value "
            "remaining in cash."
        )
    elif cash_allocation >= 50:
        cash_sentence = (
            "The portfolio maintains a substantial cash reserve "
            f"of {_format_percent(cash_allocation)}."
        )
    else:
        cash_sentence = (
            "Most portfolio capital is currently invested, with "
            f"{_format_percent(cash_allocation)} remaining in cash."
        )

    if position_count == 0:
        concentration_sentence = (
            "There are no open positions to evaluate."
        )
    elif position_count == 1:
        symbol = positions[0].get("symbol", "one asset")
        concentration_sentence = (
            f"The invested portion is concentrated entirely in "
            f"{symbol}, which creates single-asset concentration "
            "risk."
        )
    else:
        concentration_sentence = (
            f"The invested portion is spread across "
            f"{position_count} open positions."
        )

    if total_gain_loss is None:
        performance_sentence = (
            "Total portfolio performance is unavailable."
        )
    elif total_gain_loss > 0:
        performance_sentence = (
            "Combined realized and unrealized performance is "
            f"positive by {_format_money(total_gain_loss)}."
        )
    elif total_gain_loss < 0:
        performance_sentence = (
            "Combined realized and unrealized performance is "
            f"negative by {_format_money(abs(total_gain_loss))}."
        )
    else:
        performance_sentence = (
            "Combined realized and unrealized performance is flat."
        )

    executive_summary = " ".join(
        [
            cash_sentence,
            concentration_sentence,
            performance_sentence,
            (
                f"Overall portfolio risk is currently classified "
                f"as {risk_level}."
            ),
        ]
    )

    return {
        "status": "success",
        "portfolio_id": portfolio_data["portfolio"]["id"],
        "generated_at": portfolio_data["generated_at"],
        "summary": executive_summary,
        "executive_summary": executive_summary,
        "risk_level": risk_level,
        "confidence_percent": confidence_percent,
        "confidence_reasons": confidence_reasons,
        "position_count": position_count,
        "cash_allocation_percent": cash_allocation,
        "invested_allocation_percent": invested_allocation,
        "unrealized_gain_loss_usd": unrealized_gain_loss,
        "realized_gain_loss_usd": realized_gain_loss,
        "total_gain_loss_usd": total_gain_loss,
        "key_observations": key_observations,
    }
