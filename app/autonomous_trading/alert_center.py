from datetime import datetime, timezone
from typing import Any

from app.autonomous_trading.decision_queries import (
    get_recent_trade_decisions,
)
from app.autonomous_trading.journal_queries import (
    get_trade_journal,
)
from app.market_db.alerts import (
    get_recent_alerts,
)
from app.market_db.portfolio_queries import (
    get_portfolio_summary,
)


def _normalize_datetime(
    value: str | None,
) -> datetime:
    if not value:
        return datetime.min.replace(
            tzinfo=timezone.utc
        )

    parsed = datetime.fromisoformat(value)

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(timezone.utc)


def _market_event(
    alert: dict[str, Any],
) -> dict[str, Any]:
    move_percent = float(
        alert.get("move_percent")
        or 0
    )

    direction = (
        "up"
        if move_percent > 0
        else "down"
        if move_percent < 0
        else "flat"
    )

    return {
        "event_id": (
            f'market-{alert["id"]}'
        ),
        "event_type": "market",
        "category": "market_move",
        "severity": alert.get(
            "severity",
            "low",
        ),
        "symbol": alert.get("symbol"),
        "title": (
            f'{alert.get("symbol")} '
            f'{direction.upper()} '
            f'{abs(move_percent):.3f}%'
        ),
        "message": alert.get("message"),
        "status": "observed",
        "move_percent": move_percent,
        "price_usd": alert.get(
            "price_usd"
        ),
        "comparison_minutes": alert.get(
            "comparison_minutes"
        ),
        "confidence_percent": None,
        "quantity": None,
        "transaction_id": None,
        "strategy_name": None,
        "rejection_reasons": [],
        "occurred_at": (
            alert.get("created_at")
            or alert.get("observed_at")
        ),
    }


def _decision_severity(
    decision: dict[str, Any],
) -> str:
    execution_status = (
        decision.get("execution_status")
        or ""
    ).lower()

    if execution_status == "failed":
        return "critical"

    if not decision.get("approved"):
        return "high"

    if execution_status == "executed":
        return "medium"

    return "low"


def _autonomous_event(
    decision: dict[str, Any],
) -> dict[str, Any]:
    action = str(
        decision.get("action")
        or "unknown"
    ).upper()

    symbol = (
        decision.get("symbol")
        or "UNKNOWN"
    )

    approved = bool(
        decision.get("approved")
    )

    execution_status = (
        decision.get("execution_status")
        or (
            "approved"
            if approved
            else "rejected"
        )
    )

    if execution_status == "failed":
        category = "execution_failed"
    elif not approved:
        category = "risk_rejected"
    elif execution_status == "executed":
        if action == "BUY":
            category = "buy_executed"
        elif action == "SELL":
            category = "sell_executed"
        else:
            category = "trade_executed"
    else:
        category = "trade_decision"

    if execution_status == "executed":
        title = (
            f"{symbol} {action} EXECUTED"
        )
    elif not approved:
        title = (
            f"{symbol} {action} REJECTED"
        )
    else:
        title = (
            f"{symbol} {action} "
            f"{execution_status.upper()}"
        )

    occurred_at = (
        decision.get("executed_at")
        or decision.get(
            "execution_attempted_at"
        )
        or decision.get("evaluated_at")
        or decision.get("created_at")
    )

    return {
        "event_id": (
            f'autonomous-'
            f'{decision["decision_id"]}'
        ),
        "event_type": "autonomous",
        "category": category,
        "severity": _decision_severity(
            decision
        ),
        "symbol": symbol,
        "title": title,
        "message": decision.get(
            "rationale"
        ),
        "status": execution_status,
        "move_percent": None,
        "price_usd": decision.get(
            "reference_price_usd"
        ),
        "comparison_minutes": None,
        "confidence_percent": (
            decision.get(
                "confidence_percent"
            )
        ),
        "quantity": decision.get(
            "quantity"
        ),
        "transaction_id": (
            decision.get(
                "portfolio_transaction_id"
            )
        ),
        "strategy_name": (
            decision.get(
                "strategy_name"
            )
        ),
        "rejection_reasons": (
            decision.get(
                "rejection_reasons"
            )
            or []
        ),
        "occurred_at": occurred_at,
    }


POSITION_GAIN_THRESHOLDS = (
    (20.0, "critical"),
    (10.0, "high"),
    (5.0, "medium"),
)

POSITION_LOSS_THRESHOLDS = (
    (-10.0, "critical"),
    (-5.0, "high"),
    (-3.0, "medium"),
)


def _position_threshold_event(
    position: dict[str, Any],
) -> dict[str, Any] | None:
    gain_loss_percent = position.get(
        "unrealized_gain_loss_percent"
    )

    if gain_loss_percent is None:
        return None

    gain_loss_percent = float(
        gain_loss_percent
    )

    threshold = None
    severity = None
    direction = None

    if gain_loss_percent >= 0:
        for candidate, candidate_severity in (
            POSITION_GAIN_THRESHOLDS
        ):
            if gain_loss_percent >= candidate:
                threshold = candidate
                severity = candidate_severity
                direction = "gain"
                break
    else:
        for candidate, candidate_severity in (
            POSITION_LOSS_THRESHOLDS
        ):
            if gain_loss_percent <= candidate:
                threshold = candidate
                severity = candidate_severity
                direction = "loss"
                break

    if threshold is None:
        return None

    symbol = (
        position.get("symbol")
        or "UNKNOWN"
    )

    position_id = position.get(
        "position_id"
    )

    threshold_label = (
        f"{abs(threshold):g}"
    )

    if direction == "gain":
        title = (
            f"{symbol} POSITION GAIN "
            f"+{gain_loss_percent:.2f}%"
        )
        message = (
            f"{symbol} crossed the "
            f"+{threshold_label}% position "
            f"gain threshold."
        )
    else:
        title = (
            f"{symbol} POSITION LOSS "
            f"{gain_loss_percent:.2f}%"
        )
        message = (
            f"{symbol} crossed the "
            f"-{threshold_label}% position "
            f"loss threshold."
        )

    return {
        "event_id": (
            f"position-{position_id}-"
            f"{direction}-{threshold_label}"
        ),
        "event_type": "portfolio",
        "category": (
            f"position_{direction}"
        ),
        "severity": severity,
        "symbol": symbol,
        "title": title,
        "message": message,
        "status": "observed",
        "move_percent": (
            gain_loss_percent
        ),
        "price_usd": position.get(
            "latest_price_usd"
        ),
        "comparison_minutes": None,
        "confidence_percent": None,
        "quantity": position.get(
            "quantity"
        ),
        "transaction_id": None,
        "strategy_name": None,
        "rejection_reasons": [],
        "threshold_percent": threshold,
        "gain_loss_usd": position.get(
            "unrealized_gain_loss_usd"
        ),
        "gain_loss_percent": (
            gain_loss_percent
        ),
        "occurred_at": (
            position.get(
                "price_observed_at"
            )
            or position.get(
                "updated_at"
            )
        ),
    }


def _exit_event(
    journal: dict[str, Any],
) -> dict[str, Any] | None:
    exit_rule = journal.get("exit_rule")

    if not exit_rule:
        return None

    symbol = (
        journal.get("symbol")
        or "UNKNOWN"
    )

    rule_titles = {
        "stop_loss": "STOP LOSS EXIT",
        "take_profit": "TAKE PROFIT EXIT",
        "momentum_reversal": "MOMENTUM REVERSAL EXIT",
        "max_holding_exposure": "MAX EXPOSURE EXIT",
        "max_position_duration": "MAX DURATION EXIT",
    }

    severity_by_rule = {
        "stop_loss": "critical",
        "take_profit": "high",
        "momentum_reversal": "high",
        "max_holding_exposure": "critical",
        "max_position_duration": "high",
    }

    title_suffix = rule_titles.get(
        exit_rule,
        "POSITION EXIT",
    )

    return {
        "event_id": (
            f'exit-{journal["id"]}-'
            f'{exit_rule}'
        ),
        "event_type": "portfolio",
        "category": "portfolio_exit",
        "severity": severity_by_rule.get(
            exit_rule,
            "high",
        ),
        "symbol": symbol,
        "title": (
            f"{symbol} {title_suffix}"
        ),
        "message": journal.get(
            "exit_rationale"
        ),
        "status": "executed",
        "move_percent": None,
        "price_usd": journal.get(
            "exit_price_usd"
        ),
        "comparison_minutes": None,
        "confidence_percent": None,
        "quantity": journal.get(
            "entry_quantity"
        ),
        "transaction_id": journal.get(
            "exit_transaction_id"
        ),
        "strategy_name": journal.get(
            "strategy_name"
        ),
        "rejection_reasons": [],
        "exit_rule": exit_rule,
        "realized_gain_loss_usd": (
            journal.get(
                "realized_gain_loss_usd"
            )
        ),
        "return_percent": journal.get(
            "return_percent"
        ),
        "occurred_at": (
            journal.get("closed_at")
            or journal.get("updated_at")
        ),
    }


def _notification_policy(
    event: dict[str, Any],
) -> dict[str, Any]:
    category = str(
        event.get("category")
        or ""
    )

    severity = str(
        event.get("severity")
        or "low"
    ).lower()

    if category == "execution_failed":
        return {
            "eligible": True,
            "priority": "critical",
            "reason": "Autonomous execution failed.",
        }

    if category in (
        "buy_executed",
        "sell_executed",
    ):
        return {
            "eligible": True,
            "priority": "high",
            "reason": "Autonomous trade executed.",
        }

    if category == "portfolio_exit":
        return {
            "eligible": True,
            "priority": "high",
            "reason": "Portfolio exit executed.",
        }

    if category in (
        "position_gain",
        "position_loss",
    ):
        eligible = severity in (
            "high",
            "critical",
        )

        return {
            "eligible": eligible,
            "priority": (
                severity
                if eligible
                else "none"
            ),
            "reason": (
                "Significant position threshold crossed."
                if eligible
                else "Position move below notification threshold."
            ),
        }

    if category == "market_move":
        eligible = severity in (
            "high",
            "critical",
        )

        return {
            "eligible": eligible,
            "priority": (
                severity
                if eligible
                else "none"
            ),
            "reason": (
                "High-severity market move."
                if eligible
                else "Routine market move."
            ),
        }

    if category == "risk_rejected":
        return {
            "eligible": False,
            "priority": "none",
            "reason": "Risk rejection retained in Alert Center.",
        }

    return {
        "eligible": False,
        "priority": "none",
        "reason": "Event does not require notification.",
    }


def get_alert_center(
    *,
    market_limit: int = 50,
    decision_limit: int = 25,
    display_limit: int = 30,
) -> dict[str, Any]:
    """
    Return a normalized feed containing market alerts,
    autonomous trading events, and portfolio events.

    This function does not modify trading state.
    """

    market_result = get_recent_alerts(
        limit=market_limit
    )

    decisions = get_recent_trade_decisions(
        limit=decision_limit
    )

    closed_journal = get_trade_journal(
        status="closed",
        limit=decision_limit,
    )

    portfolio_summary = (
        get_portfolio_summary()
    )

    events = [
        _market_event(alert)
        for alert in (
            market_result.get("alerts")
            or []
        )
    ]

    events.extend(
        _autonomous_event(decision)
        for decision in decisions
    )

    position_events = [
        _position_threshold_event(position)
        for position in (
            portfolio_summary.get("positions")
            or []
        )
    ]

    events.extend(
        event
        for event in position_events
        if event is not None
    )

    exit_events = [
        _exit_event(journal)
        for journal in (
            closed_journal.get("journals")
            or []
        )
    ]

    events.extend(
        event
        for event in exit_events
        if event is not None
    )

    for event in events:
        notification = (
            _notification_policy(event)
        )

        event["notification_eligible"] = (
            notification["eligible"]
        )

        event["notification_priority"] = (
            notification["priority"]
        )

        event["notification_reason"] = (
            notification["reason"]
        )

    portfolio_exit_transaction_ids = {
        event.get("transaction_id")
        for event in events
        if (
            event.get("category")
            == "portfolio_exit"
            and event.get("transaction_id")
            is not None
        )
    }

    for event in events:
        if (
            event.get("category")
            == "sell_executed"
            and event.get("transaction_id")
            in portfolio_exit_transaction_ids
        ):
            event[
                "notification_eligible"
            ] = False

            event[
                "notification_priority"
            ] = "none"

            event[
                "notification_reason"
            ] = (
                "Related portfolio exit "
                "provides the notification."
            )

    events.sort(
        key=lambda event: (
            _normalize_datetime(
                event.get("occurred_at")
            )
        ),
        reverse=True,
    )

    displayed_events = events[
        :display_limit
    ]

    severity_counts = {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
    }

    category_counts = {
        "market": 0,
        "autonomous": 0,
        "portfolio": 0,
        "position_gain": 0,
        "position_loss": 0,
        "portfolio_exit": 0,
        "buy_executed": 0,
        "sell_executed": 0,
        "risk_rejected": 0,
        "execution_failed": 0,
    }

    for event in events:
        severity = event.get(
            "severity",
            "low",
        )

        if severity in severity_counts:
            severity_counts[severity] += 1

        event_type = event.get(
            "event_type"
        )

        if event_type in category_counts:
            category_counts[event_type] += 1


        category = event.get(
            "category"
        )

        if category in category_counts:
            category_counts[category] += 1

    return {
        "status": "success",
        "event_count": len(events),
        "displayed_count": len(
            displayed_events
        ),
        "severity_counts": severity_counts,
        "category_counts": category_counts,
        "events": displayed_events,
    }
