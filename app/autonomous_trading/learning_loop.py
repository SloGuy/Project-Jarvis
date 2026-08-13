from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import (
    AutonomousTradeJournal,
    MarketAsset,
)


ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")


def _to_decimal(
    value: object,
) -> Decimal:
    if value is None:
        return ZERO

    return Decimal(str(value))


def get_learning_summary() -> dict[str, Any]:
    """
    Analyze completed autonomous trade journal records.

    This function is read-only.
    It does not modify strategies, policies, or trades.
    """

    with SessionLocal() as session:
        journals = session.scalars(
            select(AutonomousTradeJournal)
            .where(
                AutonomousTradeJournal.status
                == "closed",
            )
            .order_by(
                AutonomousTradeJournal.closed_at.asc()
            )
        ).all()

    total_trades = len(journals)

    if total_trades == 0:
        return {
            "status": "success",
            "closed_trade_count": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "win_rate_percent": None,
            "total_realized_gain_loss_usd": 0.0,
            "average_realized_gain_loss_usd": None,
            "average_return_percent": None,
            "average_holding_duration_seconds": None,
            "thesis_accuracy_percent": None,
        }

    wins = 0
    losses = 0
    breakeven = 0

    total_realized = ZERO
    total_return = ZERO

    return_count = 0

    total_holding_seconds = 0
    holding_count = 0

    thesis_correct_count = 0
    thesis_evaluated_count = 0

    for journal in journals:
        realized = _to_decimal(
            journal.realized_gain_loss_usd
        )

        total_realized += realized

        if realized > ZERO:
            wins += 1
        elif realized < ZERO:
            losses += 1
        else:
            breakeven += 1

        if journal.return_percent is not None:
            total_return += _to_decimal(
                journal.return_percent
            )
            return_count += 1

        if journal.holding_duration_seconds is not None:
            total_holding_seconds += (
                journal.holding_duration_seconds
            )
            holding_count += 1

        if journal.thesis_correct is not None:
            thesis_evaluated_count += 1

            if journal.thesis_correct:
                thesis_correct_count += 1

    win_rate_percent = (
        Decimal(wins)
        / Decimal(total_trades)
        * ONE_HUNDRED
    )

    average_realized = (
        total_realized
        / Decimal(total_trades)
    )

    average_return = None

    if return_count > 0:
        average_return = (
            total_return
            / Decimal(return_count)
        )

    average_holding_duration_seconds = None

    if holding_count > 0:
        average_holding_duration_seconds = (
            total_holding_seconds
            / holding_count
        )

    thesis_accuracy_percent = None

    if thesis_evaluated_count > 0:
        thesis_accuracy_percent = (
            Decimal(thesis_correct_count)
            / Decimal(thesis_evaluated_count)
            * ONE_HUNDRED
        )

    return {
        "status": "success",
        "closed_trade_count": total_trades,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate_percent": float(
            win_rate_percent
        ),
        "total_realized_gain_loss_usd": float(
            total_realized
        ),
        "average_realized_gain_loss_usd": float(
            average_realized
        ),
        "average_return_percent": (
            float(average_return)
            if average_return is not None
            else None
        ),
        "average_holding_duration_seconds": (
            average_holding_duration_seconds
        ),
        "thesis_accuracy_percent": (
            float(thesis_accuracy_percent)
            if thesis_accuracy_percent is not None
            else None
        ),
    }


def get_strategy_performance() -> dict[str, Any]:
    """
    Summarize completed journal performance by strategy.
    """

    with SessionLocal() as session:
        journals = session.scalars(
            select(AutonomousTradeJournal)
            .where(
                AutonomousTradeJournal.status
                == "closed",
            )
            .order_by(
                AutonomousTradeJournal.closed_at.asc()
            )
        ).all()

    strategies: dict[str, dict[str, Any]] = {}

    for journal in journals:
        strategy_name = journal.strategy_name

        if strategy_name not in strategies:
            strategies[strategy_name] = {
                "strategy_name": strategy_name,
                "trade_count": 0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "total_realized_gain_loss_usd": ZERO,
                "total_return_percent": ZERO,
                "return_count": 0,
                "thesis_correct_count": 0,
                "thesis_evaluated_count": 0,
            }

        bucket = strategies[strategy_name]

        realized = _to_decimal(
            journal.realized_gain_loss_usd
        )

        bucket["trade_count"] += 1
        bucket[
            "total_realized_gain_loss_usd"
        ] += realized

        if realized > ZERO:
            bucket["wins"] += 1
        elif realized < ZERO:
            bucket["losses"] += 1
        else:
            bucket["breakeven"] += 1

        if journal.return_percent is not None:
            bucket["total_return_percent"] += (
                _to_decimal(
                    journal.return_percent
                )
            )
            bucket["return_count"] += 1

        if journal.thesis_correct is not None:
            bucket["thesis_evaluated_count"] += 1

            if journal.thesis_correct:
                bucket["thesis_correct_count"] += 1

    results = []

    for bucket in strategies.values():
        trade_count = bucket["trade_count"]

        win_rate_percent = (
            Decimal(bucket["wins"])
            / Decimal(trade_count)
            * ONE_HUNDRED
        )

        average_return_percent = None

        if bucket["return_count"] > 0:
            average_return_percent = (
                bucket["total_return_percent"]
                / Decimal(
                    bucket["return_count"]
                )
            )

        thesis_accuracy_percent = None

        if bucket["thesis_evaluated_count"] > 0:
            thesis_accuracy_percent = (
                Decimal(
                    bucket["thesis_correct_count"]
                )
                / Decimal(
                    bucket["thesis_evaluated_count"]
                )
                * ONE_HUNDRED
            )

        results.append(
            {
                "strategy_name": bucket[
                    "strategy_name"
                ],
                "trade_count": trade_count,
                "wins": bucket["wins"],
                "losses": bucket["losses"],
                "breakeven": bucket[
                    "breakeven"
                ],
                "win_rate_percent": float(
                    win_rate_percent
                ),
                "total_realized_gain_loss_usd": float(
                    bucket[
                        "total_realized_gain_loss_usd"
                    ]
                ),
                "average_return_percent": (
                    float(
                        average_return_percent
                    )
                    if (
                        average_return_percent
                        is not None
                    )
                    else None
                ),
                "thesis_accuracy_percent": (
                    float(
                        thesis_accuracy_percent
                    )
                    if (
                        thesis_accuracy_percent
                        is not None
                    )
                    else None
                ),
            }
        )

    results.sort(
        key=lambda row: (
            row[
                "total_realized_gain_loss_usd"
            ]
        ),
        reverse=True,
    )

    return {
        "status": "success",
        "strategy_count": len(results),
        "strategies": results,
    }


def get_symbol_performance() -> dict[str, Any]:
    """
    Summarize completed journal performance by asset symbol.
    """

    with SessionLocal() as session:
        rows = session.execute(
            select(
                AutonomousTradeJournal,
                MarketAsset.symbol,
            )
            .join(
                MarketAsset,
                MarketAsset.id
                == AutonomousTradeJournal.asset_id,
            )
            .where(
                AutonomousTradeJournal.status
                == "closed",
            )
            .order_by(
                AutonomousTradeJournal.closed_at.asc()
            )
        ).all()

    symbols: dict[str, dict[str, Any]] = {}

    for journal, symbol in rows:
        symbol = str(symbol).upper()

        if symbol not in symbols:
            symbols[symbol] = {
                "symbol": symbol,
                "trade_count": 0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "total_realized_gain_loss_usd": ZERO,
                "total_return_percent": ZERO,
                "return_count": 0,
                "thesis_correct_count": 0,
                "thesis_evaluated_count": 0,
            }

        bucket = symbols[symbol]

        realized = _to_decimal(
            journal.realized_gain_loss_usd
        )

        bucket["trade_count"] += 1
        bucket[
            "total_realized_gain_loss_usd"
        ] += realized

        if realized > ZERO:
            bucket["wins"] += 1
        elif realized < ZERO:
            bucket["losses"] += 1
        else:
            bucket["breakeven"] += 1

        if journal.return_percent is not None:
            bucket["total_return_percent"] += (
                _to_decimal(
                    journal.return_percent
                )
            )
            bucket["return_count"] += 1

        if journal.thesis_correct is not None:
            bucket["thesis_evaluated_count"] += 1

            if journal.thesis_correct:
                bucket["thesis_correct_count"] += 1

    results = []

    for bucket in symbols.values():
        trade_count = bucket["trade_count"]

        win_rate_percent = (
            Decimal(bucket["wins"])
            / Decimal(trade_count)
            * ONE_HUNDRED
        )

        average_return_percent = None

        if bucket["return_count"] > 0:
            average_return_percent = (
                bucket["total_return_percent"]
                / Decimal(
                    bucket["return_count"]
                )
            )

        thesis_accuracy_percent = None

        if bucket["thesis_evaluated_count"] > 0:
            thesis_accuracy_percent = (
                Decimal(
                    bucket["thesis_correct_count"]
                )
                / Decimal(
                    bucket["thesis_evaluated_count"]
                )
                * ONE_HUNDRED
            )

        results.append(
            {
                "symbol": bucket["symbol"],
                "trade_count": trade_count,
                "wins": bucket["wins"],
                "losses": bucket["losses"],
                "breakeven": bucket["breakeven"],
                "win_rate_percent": float(
                    win_rate_percent
                ),
                "total_realized_gain_loss_usd": float(
                    bucket[
                        "total_realized_gain_loss_usd"
                    ]
                ),
                "average_return_percent": (
                    float(
                        average_return_percent
                    )
                    if average_return_percent
                    is not None
                    else None
                ),
                "thesis_accuracy_percent": (
                    float(
                        thesis_accuracy_percent
                    )
                    if thesis_accuracy_percent
                    is not None
                    else None
                ),
            }
        )

    results.sort(
        key=lambda row: (
            row[
                "total_realized_gain_loss_usd"
            ]
        ),
        reverse=True,
    )

    return {
        "status": "success",
        "symbol_count": len(results),
        "symbols": results,
    }


def get_exit_rule_performance() -> dict[str, Any]:
    """
    Summarize completed journal performance by exit rule.
    """

    with SessionLocal() as session:
        journals = session.scalars(
            select(AutonomousTradeJournal)
            .where(
                AutonomousTradeJournal.status
                == "closed",
            )
            .order_by(
                AutonomousTradeJournal.closed_at.asc()
            )
        ).all()

    exit_rules: dict[str, dict[str, Any]] = {}

    for journal in journals:
        rule_name = (
            journal.exit_rule
            or "unspecified"
        )

        if rule_name not in exit_rules:
            exit_rules[rule_name] = {
                "exit_rule": rule_name,
                "trade_count": 0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "total_realized_gain_loss_usd": ZERO,
                "total_return_percent": ZERO,
                "return_count": 0,
            }

        bucket = exit_rules[rule_name]

        realized = _to_decimal(
            journal.realized_gain_loss_usd
        )

        bucket["trade_count"] += 1
        bucket[
            "total_realized_gain_loss_usd"
        ] += realized

        if realized > ZERO:
            bucket["wins"] += 1
        elif realized < ZERO:
            bucket["losses"] += 1
        else:
            bucket["breakeven"] += 1

        if journal.return_percent is not None:
            bucket["total_return_percent"] += (
                _to_decimal(
                    journal.return_percent
                )
            )
            bucket["return_count"] += 1

    results = []

    for bucket in exit_rules.values():
        trade_count = bucket["trade_count"]

        win_rate_percent = (
            Decimal(bucket["wins"])
            / Decimal(trade_count)
            * ONE_HUNDRED
        )

        average_return_percent = None

        if bucket["return_count"] > 0:
            average_return_percent = (
                bucket["total_return_percent"]
                / Decimal(
                    bucket["return_count"]
                )
            )

        results.append(
            {
                "exit_rule": bucket["exit_rule"],
                "trade_count": trade_count,
                "wins": bucket["wins"],
                "losses": bucket["losses"],
                "breakeven": bucket[
                    "breakeven"
                ],
                "win_rate_percent": float(
                    win_rate_percent
                ),
                "total_realized_gain_loss_usd": float(
                    bucket[
                        "total_realized_gain_loss_usd"
                    ]
                ),
                "average_return_percent": (
                    float(
                        average_return_percent
                    )
                    if average_return_percent
                    is not None
                    else None
                ),
            }
        )

    results.sort(
        key=lambda row: (
            row[
                "total_realized_gain_loss_usd"
            ]
        ),
        reverse=True,
    )

    return {
        "status": "success",
        "exit_rule_count": len(results),
        "exit_rules": results,
    }


def get_confidence_performance() -> dict[str, Any]:
    """
    Summarize completed journal performance by entry confidence bucket.
    """

    with SessionLocal() as session:
        journals = session.scalars(
            select(AutonomousTradeJournal)
            .where(
                AutonomousTradeJournal.status
                == "closed",
            )
            .order_by(
                AutonomousTradeJournal.closed_at.asc()
            )
        ).all()

    buckets = {
        "70-74": {
            "minimum": Decimal("70"),
            "maximum": Decimal("75"),
        },
        "75-79": {
            "minimum": Decimal("75"),
            "maximum": Decimal("80"),
        },
        "80-89": {
            "minimum": Decimal("80"),
            "maximum": Decimal("90"),
        },
        "90+": {
            "minimum": Decimal("90"),
            "maximum": None,
        },
    }

    results_by_bucket: dict[str, dict[str, Any]] = {}

    for name in buckets:
        results_by_bucket[name] = {
            "confidence_bucket": name,
            "trade_count": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "total_realized_gain_loss_usd": ZERO,
            "total_return_percent": ZERO,
            "return_count": 0,
        }

    for journal in journals:
        confidence = _to_decimal(
            journal.entry_confidence_percent
        )

        selected_bucket = None

        for name, limits in buckets.items():
            minimum = limits["minimum"]
            maximum = limits["maximum"]

            if confidence < minimum:
                continue

            if (
                maximum is not None
                and confidence >= maximum
            ):
                continue

            selected_bucket = name
            break

        if selected_bucket is None:
            continue

        bucket = results_by_bucket[
            selected_bucket
        ]

        realized = _to_decimal(
            journal.realized_gain_loss_usd
        )

        bucket["trade_count"] += 1
        bucket[
            "total_realized_gain_loss_usd"
        ] += realized

        if realized > ZERO:
            bucket["wins"] += 1
        elif realized < ZERO:
            bucket["losses"] += 1
        else:
            bucket["breakeven"] += 1

        if journal.return_percent is not None:
            bucket["total_return_percent"] += (
                _to_decimal(
                    journal.return_percent
                )
            )
            bucket["return_count"] += 1

    results = []

    for bucket in results_by_bucket.values():
        trade_count = bucket["trade_count"]

        if trade_count == 0:
            results.append(
                {
                    "confidence_bucket": bucket[
                        "confidence_bucket"
                    ],
                    "trade_count": 0,
                    "wins": 0,
                    "losses": 0,
                    "breakeven": 0,
                    "win_rate_percent": None,
                    "total_realized_gain_loss_usd": 0.0,
                    "average_return_percent": None,
                }
            )
            continue

        win_rate_percent = (
            Decimal(bucket["wins"])
            / Decimal(trade_count)
            * ONE_HUNDRED
        )

        average_return_percent = None

        if bucket["return_count"] > 0:
            average_return_percent = (
                bucket["total_return_percent"]
                / Decimal(
                    bucket["return_count"]
                )
            )

        results.append(
            {
                "confidence_bucket": bucket[
                    "confidence_bucket"
                ],
                "trade_count": trade_count,
                "wins": bucket["wins"],
                "losses": bucket["losses"],
                "breakeven": bucket[
                    "breakeven"
                ],
                "win_rate_percent": float(
                    win_rate_percent
                ),
                "total_realized_gain_loss_usd": float(
                    bucket[
                        "total_realized_gain_loss_usd"
                    ]
                ),
                "average_return_percent": (
                    float(
                        average_return_percent
                    )
                    if average_return_percent
                    is not None
                    else None
                ),
            }
        )

    return {
        "status": "success",
        "confidence_buckets": results,
    }


def get_learning_report() -> dict[str, Any]:
    """
    Return the complete read-only autonomous trading learning report.
    """

    return {
        "status": "success",
        "summary": get_learning_summary(),
        "by_strategy": get_strategy_performance(),
        "by_symbol": get_symbol_performance(),
        "by_exit_rule": get_exit_rule_performance(),
        "by_confidence": get_confidence_performance(),
    }
