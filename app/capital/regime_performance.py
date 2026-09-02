from datetime import (
    datetime,
    timedelta,
)
from typing import Any

from app.autonomous_trading.journal_queries import (
    get_trade_journal,
)
from app.capital.benchmark import (
    get_benchmark_performance,
)

from app.capital.market_regime import (
    classify_market_regime_at,
)


def aggregate_regime_performance(
    *,
    journals: list[dict[str, Any]],
    benchmark_series: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    buckets: dict[
        tuple[str, str],
        dict[str, Any],
    ] = {}

    for journal in journals:
        if journal.get("status") != "closed":
            continue

        strategy_name = journal["strategy_name"]

        regime = classify_market_regime_at(
            series=benchmark_series,
            observed_at=datetime.fromisoformat(
                journal["opened_at"]
            ),
        )

        regime_name = regime["combined_regime"]
        key = (strategy_name, regime_name)

        bucket = buckets.setdefault(
            key,
            {
                "strategy_name": strategy_name,
                "regime": regime_name,
                "trade_count": 0,
                "winning_trade_count": 0,
                "losing_trade_count": 0,
                "breakeven_trade_count": 0,
                "total_realized_gain_loss_usd": 0.0,
                "total_return_percent": 0.0,
            },
        )

        gain_loss = float(
            journal.get(
                "realized_gain_loss_usd"
            )
            or 0
        )

        trade_return = float(
            journal.get("return_percent")
            or 0
        )

        bucket["trade_count"] += 1
        bucket[
            "total_realized_gain_loss_usd"
        ] += gain_loss
        bucket["total_return_percent"] += (
            trade_return
        )

        if gain_loss > 0:
            bucket["winning_trade_count"] += 1
        elif gain_loss < 0:
            bucket["losing_trade_count"] += 1
        else:
            bucket[
                "breakeven_trade_count"
            ] += 1

    results = []

    for bucket in buckets.values():
        trade_count = bucket["trade_count"]
        winning_count = bucket[
            "winning_trade_count"
        ]

        results.append(
            {
                **bucket,
                "total_realized_gain_loss_usd": round(
                    bucket[
                        "total_realized_gain_loss_usd"
                    ],
                    6,
                ),
                "total_return_percent": round(
                    bucket[
                        "total_return_percent"
                    ],
                    6,
                ),
                "win_rate_percent": round(
                    winning_count
                    / trade_count
                    * 100,
                    2,
                ),
                "expectancy_usd": round(
                    bucket[
                        "total_realized_gain_loss_usd"
                    ]
                    / trade_count,
                    6,
                ),
                "average_return_percent": round(
                    bucket[
                        "total_return_percent"
                    ]
                    / trade_count,
                    6,
                ),
            }
        )

    results.sort(
        key=lambda result: (
            result["strategy_name"],
            -result["trade_count"],
            result["regime"],
        )
    )

    return results


def get_regime_performance() -> dict[str, Any]:
    journal_result = get_trade_journal(
        status="closed",
        limit=10000,
    )

    journals = journal_result["journals"]

    if not journals:
        return {
            "status": "success",
            "attribution_basis": "entry_regime",
            "closed_trade_count": 0,
            "classified_trade_count": 0,
            "uncertain_trade_count": 0,
            "results": [],
            "database_writes": False,
        }

    earliest_opened_at = min(
        datetime.fromisoformat(
            journal["opened_at"]
        )
        for journal in journals
    )

    benchmark = get_benchmark_performance(
        started_at=(
            earliest_opened_at
            - timedelta(days=90)
        )
    )

    if benchmark.get("status") != "success":
        return {
            "status": "unavailable",
            "reason": benchmark.get(
                "reason",
                "Benchmark history is unavailable.",
            ),
            "attribution_basis": "entry_regime",
            "closed_trade_count": len(journals),
            "classified_trade_count": 0,
            "uncertain_trade_count": (
                len(journals)
            ),
            "results": [],
            "database_writes": False,
        }

    results = aggregate_regime_performance(
        journals=journals,
        benchmark_series=benchmark["series"],
    )

    uncertain_count = sum(
        result["trade_count"]
        for result in results
        if result["regime"] == "uncertain"
    )

    return {
        "status": "success",
        "attribution_basis": "entry_regime",
        "regime_methodology_version": (
            "spy_20_session_v1"
        ),
        "closed_trade_count": len(journals),
        "classified_trade_count": (
            len(journals) - uncertain_count
        ),
        "uncertain_trade_count": (
            uncertain_count
        ),
        "results": results,
        "database_writes": False,
        "allocation_authority": False,
        "live_capital_authority": False,
    }
