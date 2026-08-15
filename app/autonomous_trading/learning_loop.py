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


def get_entry_pattern_performance() -> dict[str, Any]:
    """
    Summarize completed trade performance by entry momentum pattern.

    This analysis is read-only.
    It does not modify strategy behavior.
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

    pattern_buckets: dict[
        str,
        dict[str, Any],
    ] = {}

    for journal in journals:
        context = (
            journal.entry_market_context
            or {}
        )

        short_term = context.get(
            "short_term_percent"
        )

        trend = context.get(
            "trend_percent"
        )

        if (
            short_term is None
            or trend is None
        ):
            pattern_name = "unknown"
        else:
            short_term_decimal = (
                _to_decimal(short_term)
            )

            trend_decimal = (
                _to_decimal(trend)
            )

            if (
                short_term_decimal
                >= Decimal("0.75")
                and trend_decimal
                >= Decimal("1.50")
            ):
                pattern_name = (
                    "strong_alignment"
                )
            elif (
                short_term_decimal
                >= Decimal("0.35")
                and trend_decimal
                >= Decimal("0.75")
            ):
                pattern_name = (
                    "moderate_alignment"
                )
            elif (
                short_term_decimal > ZERO
                and trend_decimal > ZERO
            ):
                pattern_name = (
                    "weak_alignment"
                )
            elif (
                short_term_decimal < ZERO
                and trend_decimal < ZERO
            ):
                pattern_name = (
                    "negative_momentum"
                )
            else:
                pattern_name = (
                    "mixed_momentum"
                )

        if pattern_name not in pattern_buckets:
            pattern_buckets[
                pattern_name
            ] = {
                "entry_pattern": (
                    pattern_name
                ),
                "trade_count": 0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "total_realized_gain_loss_usd": (
                    ZERO
                ),
                "total_return_percent": ZERO,
                "return_count": 0,
                "thesis_correct_count": 0,
                "thesis_failed_count": 0,
                "thesis_inconclusive_count": 0,
            }

        bucket = pattern_buckets[
            pattern_name
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
            bucket[
                "total_return_percent"
            ] += _to_decimal(
                journal.return_percent
            )

            bucket["return_count"] += 1

        if journal.thesis_correct is True:
            bucket[
                "thesis_correct_count"
            ] += 1
        elif journal.thesis_correct is False:
            bucket[
                "thesis_failed_count"
            ] += 1
        else:
            bucket[
                "thesis_inconclusive_count"
            ] += 1

    results = []

    for bucket in pattern_buckets.values():
        trade_count = bucket[
            "trade_count"
        ]

        win_rate_percent = (
            Decimal(bucket["wins"])
            / Decimal(trade_count)
            * ONE_HUNDRED
        )

        average_return_percent = None

        if bucket["return_count"] > 0:
            average_return_percent = (
                bucket[
                    "total_return_percent"
                ]
                / Decimal(
                    bucket[
                        "return_count"
                    ]
                )
            )

        results.append(
            {
                "entry_pattern": (
                    bucket[
                        "entry_pattern"
                    ]
                ),
                "trade_count": trade_count,
                "wins": bucket["wins"],
                "losses": bucket["losses"],
                "breakeven": (
                    bucket["breakeven"]
                ),
                "win_rate_percent": float(
                    win_rate_percent
                ),
                "total_realized_gain_loss_usd": (
                    float(
                        bucket[
                            "total_realized_gain_loss_usd"
                        ]
                    )
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
                "thesis_correct_count": (
                    bucket[
                        "thesis_correct_count"
                    ]
                ),
                "thesis_failed_count": (
                    bucket[
                        "thesis_failed_count"
                    ]
                ),
                "thesis_inconclusive_count": (
                    bucket[
                        "thesis_inconclusive_count"
                    ]
                ),
            }
        )

    results.sort(
        key=lambda row: (
            row["trade_count"],
            row[
                "total_realized_gain_loss_usd"
            ],
        ),
        reverse=True,
    )

    return {
        "status": "success",
        "pattern_count": len(results),
        "entry_patterns": results,
    }


def get_entry_pattern_lessons() -> dict[str, Any]:
    """
    Convert entry-pattern performance into cautious,
    read-only learning observations.
    """

    performance = (
        get_entry_pattern_performance()
    )

    patterns = performance.get(
        "entry_patterns",
        [],
    )

    lessons = []

    for pattern in patterns:
        trade_count = int(
            pattern.get("trade_count")
            or 0
        )

        wins = int(
            pattern.get("wins")
            or 0
        )

        losses = int(
            pattern.get("losses")
            or 0
        )

        thesis_failed_count = int(
            pattern.get(
                "thesis_failed_count"
            )
            or 0
        )

        win_rate = pattern.get(
            "win_rate_percent"
        )

        average_return = pattern.get(
            "average_return_percent"
        )

        pattern_name = str(
            pattern.get(
                "entry_pattern"
            )
            or "unknown"
        )

        if trade_count < 5:
            evidence_strength = "very_low"
        elif trade_count < 15:
            evidence_strength = "low"
        elif trade_count < 30:
            evidence_strength = "moderate"
        else:
            evidence_strength = "strong"

        if trade_count == 0:
            continue

        if (
            losses == trade_count
            and thesis_failed_count
            == trade_count
        ):
            observation = (
                f"All {trade_count} completed trades "
                f"in the {pattern_name} entry pattern "
                f"have been losses and all failed "
                f"their original thesis."
            )
        elif (
            wins == trade_count
            and trade_count > 0
        ):
            observation = (
                f"All {trade_count} completed trades "
                f"in the {pattern_name} entry pattern "
                f"have been profitable."
            )
        else:
            win_rate_text = (
                f"{float(win_rate):.1f}%"
                if win_rate is not None
                else "unavailable"
            )

            observation = (
                f"The {pattern_name} entry pattern "
                f"has produced a {win_rate_text} "
                f"win rate across {trade_count} "
                f"completed trades."
            )

        if average_return is not None:
            observation += (
                f" Average return is "
                f"{float(average_return):+.2f}%."
            )

        if evidence_strength in (
            "very_low",
            "low",
        ):
            recommendation_status = (
                "insufficient_evidence"
            )

            actionable = False

            recommendation = (
                "Continue collecting evidence. "
                "Do not alter strategy parameters "
                "from this combined pattern yet."
            )

        elif evidence_strength == "moderate":
            recommendation_status = (
                "review_candidate"
            )

            actionable = False

            recommendation = (
                "This combined pattern has enough "
                "evidence to justify manual strategy "
                "review, but no automatic parameter "
                "change is allowed."
            )

        else:
            recommendation_status = (
                "actionable_candidate"
            )

            actionable = True

            recommendation = (
                "This combined pattern has enough "
                "evidence to justify a formal strategy "
                "change proposal. Any change must still "
                "pass review, simulation, and risk "
                "controls before activation."
            )

        lessons.append(
            {
                "lesson_type": (
                    "entry_pattern_performance"
                ),
                "entry_pattern": (
                    pattern_name
                ),
                "trade_count": trade_count,
                "evidence_strength": (
                    evidence_strength
                ),
                "observation": observation,
                "recommendation": (
                    recommendation
                ),
                "recommendation_status": (
                    recommendation_status
                ),
                "actionable": actionable,
                "strategy_change_allowed": False,
            }
        )

    return {
        "status": "success",
        "lesson_count": len(lessons),
        "lessons": lessons,
    }


def get_combined_pattern_performance() -> dict[str, Any]:
    """
    Analyze completed trades across multiple dimensions:
    symbol, entry momentum pattern, confidence bucket,
    and exit rule.

    This analysis is read-only.
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

    buckets: dict[
        tuple[str, str, str, str],
        dict[str, Any],
    ] = {}

    for journal, symbol in rows:
        symbol = str(symbol).upper()

        context = (
            journal.entry_market_context
            or {}
        )

        short_term = context.get(
            "short_term_percent"
        )

        trend = context.get(
            "trend_percent"
        )

        if (
            short_term is None
            or trend is None
        ):
            entry_pattern = "unknown"
        else:
            short_term_decimal = (
                _to_decimal(short_term)
            )

            trend_decimal = (
                _to_decimal(trend)
            )

            if (
                short_term_decimal
                >= Decimal("0.75")
                and trend_decimal
                >= Decimal("1.50")
            ):
                entry_pattern = (
                    "strong_alignment"
                )
            elif (
                short_term_decimal
                >= Decimal("0.35")
                and trend_decimal
                >= Decimal("0.75")
            ):
                entry_pattern = (
                    "moderate_alignment"
                )
            elif (
                short_term_decimal > ZERO
                and trend_decimal > ZERO
            ):
                entry_pattern = (
                    "weak_alignment"
                )
            elif (
                short_term_decimal < ZERO
                and trend_decimal < ZERO
            ):
                entry_pattern = (
                    "negative_momentum"
                )
            else:
                entry_pattern = (
                    "mixed_momentum"
                )

        confidence = _to_decimal(
            journal.entry_confidence_percent
        )

        if (
            confidence
            >= Decimal("70")
            and confidence
            < Decimal("75")
        ):
            confidence_bucket = "70-74"
        elif (
            confidence
            >= Decimal("75")
            and confidence
            < Decimal("80")
        ):
            confidence_bucket = "75-79"
        elif (
            confidence
            >= Decimal("80")
            and confidence
            < Decimal("90")
        ):
            confidence_bucket = "80-89"
        elif confidence >= Decimal("90"):
            confidence_bucket = "90+"
        else:
            confidence_bucket = "below_70"

        exit_rule = str(
            journal.exit_rule
            or "unspecified"
        )

        key = (
            symbol,
            entry_pattern,
            confidence_bucket,
            exit_rule,
        )

        if key not in buckets:
            buckets[key] = {
                "symbol": symbol,
                "entry_pattern": entry_pattern,
                "confidence_bucket": (
                    confidence_bucket
                ),
                "exit_rule": exit_rule,
                "trade_count": 0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "total_realized_gain_loss_usd": (
                    ZERO
                ),
                "total_return_percent": ZERO,
                "return_count": 0,
                "thesis_correct_count": 0,
                "thesis_failed_count": 0,
                "thesis_inconclusive_count": 0,
            }

        bucket = buckets[key]

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
            bucket[
                "total_return_percent"
            ] += _to_decimal(
                journal.return_percent
            )

            bucket["return_count"] += 1

        if journal.thesis_correct is True:
            bucket[
                "thesis_correct_count"
            ] += 1
        elif journal.thesis_correct is False:
            bucket[
                "thesis_failed_count"
            ] += 1
        else:
            bucket[
                "thesis_inconclusive_count"
            ] += 1

    results = []

    for bucket in buckets.values():
        trade_count = bucket[
            "trade_count"
        ]

        win_rate_percent = (
            Decimal(bucket["wins"])
            / Decimal(trade_count)
            * ONE_HUNDRED
        )

        average_return_percent = None

        if bucket["return_count"] > 0:
            average_return_percent = (
                bucket[
                    "total_return_percent"
                ]
                / Decimal(
                    bucket[
                        "return_count"
                    ]
                )
            )

        if trade_count < 5:
            evidence_strength = "very_low"
        elif trade_count < 15:
            evidence_strength = "low"
        elif trade_count < 30:
            evidence_strength = "moderate"
        else:
            evidence_strength = "strong"

        results.append(
            {
                "symbol": bucket["symbol"],
                "entry_pattern": (
                    bucket[
                        "entry_pattern"
                    ]
                ),
                "confidence_bucket": (
                    bucket[
                        "confidence_bucket"
                    ]
                ),
                "exit_rule": (
                    bucket["exit_rule"]
                ),
                "trade_count": trade_count,
                "wins": bucket["wins"],
                "losses": bucket["losses"],
                "breakeven": (
                    bucket["breakeven"]
                ),
                "win_rate_percent": float(
                    win_rate_percent
                ),
                "total_realized_gain_loss_usd": (
                    float(
                        bucket[
                            "total_realized_gain_loss_usd"
                        ]
                    )
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
                "thesis_correct_count": (
                    bucket[
                        "thesis_correct_count"
                    ]
                ),
                "thesis_failed_count": (
                    bucket[
                        "thesis_failed_count"
                    ]
                ),
                "thesis_inconclusive_count": (
                    bucket[
                        "thesis_inconclusive_count"
                    ]
                ),
                "evidence_strength": (
                    evidence_strength
                ),
                "strategy_change_allowed": (
                    False
                ),
            }
        )

    results.sort(
        key=lambda row: (
            row["trade_count"],
            row[
                "total_realized_gain_loss_usd"
            ],
        ),
        reverse=True,
    )

    return {
        "status": "success",
        "combination_count": len(results),
        "patterns": results,
    }


def get_combined_pattern_lessons() -> dict[str, Any]:
    """
    Convert combined-pattern performance into cautious,
    read-only learning observations.
    """

    performance = (
        get_combined_pattern_performance()
    )

    patterns = performance.get(
        "patterns",
        [],
    )

    lessons = []

    for pattern in patterns:
        trade_count = int(
            pattern.get("trade_count")
            or 0
        )

        if trade_count == 0:
            continue

        wins = int(
            pattern.get("wins")
            or 0
        )

        losses = int(
            pattern.get("losses")
            or 0
        )

        thesis_failed_count = int(
            pattern.get(
                "thesis_failed_count"
            )
            or 0
        )

        symbol = str(
            pattern.get("symbol")
            or "UNKNOWN"
        )

        entry_pattern = str(
            pattern.get(
                "entry_pattern"
            )
            or "unknown"
        )

        confidence_bucket = str(
            pattern.get(
                "confidence_bucket"
            )
            or "unknown"
        )

        exit_rule = str(
            pattern.get("exit_rule")
            or "unspecified"
        )

        evidence_strength = str(
            pattern.get(
                "evidence_strength"
            )
            or "very_low"
        )

        win_rate = pattern.get(
            "win_rate_percent"
        )

        average_return = pattern.get(
            "average_return_percent"
        )

        if (
            losses == trade_count
            and thesis_failed_count
            == trade_count
        ):
            observation = (
                f"All {trade_count} completed {symbol} "
                f"trades entered under the "
                f"{entry_pattern} pattern with "
                f"{confidence_bucket} confidence "
                f"have been losses, and all failed "
                f"their original thesis. "
                f"All exited through "
                f"{exit_rule.replace('_', ' ')}."
            )
        elif wins == trade_count:
            observation = (
                f"All {trade_count} completed {symbol} "
                f"trades entered under the "
                f"{entry_pattern} pattern with "
                f"{confidence_bucket} confidence "
                f"have been profitable."
            )
        else:
            win_rate_text = (
                f"{float(win_rate):.1f}%"
                if win_rate is not None
                else "unavailable"
            )

            observation = (
                f"{symbol} trades entered under the "
                f"{entry_pattern} pattern with "
                f"{confidence_bucket} confidence "
                f"have produced a {win_rate_text} "
                f"win rate across {trade_count} "
                f"completed trades."
            )

        if average_return is not None:
            observation += (
                f" Average return is "
                f"{float(average_return):+.2f}%."
            )

        if evidence_strength in (
            "very_low",
            "low",
        ):
            recommendation_status = (
                "insufficient_evidence"
            )

            actionable = False

            recommendation = (
                "Continue collecting evidence. "
                "Do not alter strategy parameters "
                "from this combined pattern yet."
            )

        elif evidence_strength == "moderate":
            recommendation_status = (
                "review_candidate"
            )

            actionable = False

            recommendation = (
                "This combined pattern has enough "
                "evidence to justify manual strategy "
                "review, but no automatic parameter "
                "change is allowed."
            )

        else:
            recommendation_status = (
                "actionable_candidate"
            )

            actionable = True

            recommendation = (
                "This combined pattern has enough "
                "evidence to justify a formal strategy "
                "change proposal. Any change must still "
                "pass review, simulation, and risk "
                "controls before activation."
            )

        lessons.append(
            {
                "lesson_type": (
                    "combined_pattern_performance"
                ),
                "symbol": symbol,
                "entry_pattern": entry_pattern,
                "confidence_bucket": (
                    confidence_bucket
                ),
                "exit_rule": exit_rule,
                "trade_count": trade_count,
                "evidence_strength": (
                    evidence_strength
                ),
                "observation": observation,
                "recommendation": (
                    recommendation
                ),
                "recommendation_status": (
                    recommendation_status
                ),
                "actionable": actionable,
                "strategy_change_allowed": False,
            }
        )

    return {
        "status": "success",
        "lesson_count": len(lessons),
        "lessons": lessons,
    }


def get_learning_decision() -> dict[str, Any]:
    """
    Summarize whether current learning evidence is
    strong enough to justify strategy review.

    This function is read-only and cannot modify
    strategy or risk parameters.
    """

    entry_lessons = (
        get_entry_pattern_lessons()
        .get("lessons", [])
    )

    combined_lessons = (
        get_combined_pattern_lessons()
        .get("lessons", [])
    )

    lessons = [
        *entry_lessons,
        *combined_lessons,
    ]

    evidence_rank = {
        "very_low": 0,
        "low": 1,
        "moderate": 2,
        "strong": 3,
    }

    highest_evidence_strength = "very_low"
    highest_rank = -1

    review_candidate_count = 0
    actionable_candidate_count = 0

    for lesson in lessons:
        evidence_strength = str(
            lesson.get(
                "evidence_strength"
            )
            or "very_low"
        )

        rank = evidence_rank.get(
            evidence_strength,
            0,
        )

        if rank > highest_rank:
            highest_rank = rank
            highest_evidence_strength = (
                evidence_strength
            )

        recommendation_status = str(
            lesson.get(
                "recommendation_status"
            )
            or "insufficient_evidence"
        )

        if (
            recommendation_status
            == "review_candidate"
        ):
            review_candidate_count += 1

        if (
            recommendation_status
            == "actionable_candidate"
        ):
            actionable_candidate_count += 1

    strategy_change_recommended = (
        actionable_candidate_count > 0
    )

    if actionable_candidate_count > 0:
        decision_status = (
            "actionable_candidate"
        )

        reason = (
            "At least one learning pattern has "
            "strong enough evidence to justify "
            "a formal strategy-change proposal. "
            "No change may occur automatically."
        )

    elif review_candidate_count > 0:
        decision_status = (
            "review_candidate"
        )

        reason = (
            "At least one learning pattern has "
            "enough evidence for manual strategy "
            "review, but not for a formal change "
            "proposal."
        )

    else:
        decision_status = (
            "insufficient_evidence"
        )

        reason = (
            "Current learning patterns do not "
            "have enough evidence to justify "
            "strategy modification."
        )

    return {
        "status": "success",
        "observation_count": len(lessons),
        "review_candidate_count": (
            review_candidate_count
        ),
        "actionable_candidate_count": (
            actionable_candidate_count
        ),
        "highest_evidence_strength": (
            highest_evidence_strength
        ),
        "decision_status": decision_status,
        "strategy_change_recommended": (
            strategy_change_recommended
        ),
        "strategy_change_allowed": False,
        "reason": reason,
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
        "by_entry_pattern": (
            get_entry_pattern_performance()
        ),
        "lessons": (
            get_entry_pattern_lessons()
        ),
        "combined_patterns": (
            get_combined_pattern_performance()
        ),
        "combined_lessons": (
            get_combined_pattern_lessons()
        ),
        "learning_decision": (
            get_learning_decision()
        ),
        "adaptation": {
            "enabled": False,
            "mode": "observe_only",
            "strategy_change_allowed": False,
        },
    }
