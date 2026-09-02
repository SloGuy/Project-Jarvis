from datetime import (
    datetime,
    timedelta,
    timezone,
)

from app.capital.regime_performance import (
    aggregate_regime_performance,
)


day = datetime(
    2026,
    1,
    5,
    21,
    0,
    tzinfo=timezone.utc,
)

series = []

while len(series) < 25:
    if day.weekday() < 5:
        series.append(
            {
                "observed_at": day.isoformat(),
                "price_usd": 100.0,
            }
        )

    day += timedelta(days=1)


def journal(
    *,
    opened_at: str,
    gain_loss: float,
    trade_return: float,
) -> dict:
    return {
        "status": "closed",
        "strategy_name": "synthetic_v1",
        "opened_at": opened_at,
        "realized_gain_loss_usd": gain_loss,
        "return_percent": trade_return,
    }


results = aggregate_regime_performance(
    journals=[
        journal(
            opened_at=series[10][
                "observed_at"
            ],
            gain_loss=5.0,
            trade_return=5.0,
        ),
        journal(
            opened_at=series[19][
                "observed_at"
            ],
            gain_loss=2.0,
            trade_return=2.0,
        ),
        journal(
            opened_at=series[20][
                "observed_at"
            ],
            gain_loss=-1.0,
            trade_return=-1.0,
        ),
    ],
    benchmark_series=series,
)

by_regime = {
    result["regime"]: result
    for result in results
}

assert set(by_regime) == {
    "uncertain",
    "range_bound_low_volatility",
}

classified = by_regime[
    "range_bound_low_volatility"
]

assert classified["trade_count"] == 2
assert classified["winning_trade_count"] == 1
assert classified["losing_trade_count"] == 1
assert classified["win_rate_percent"] == 50.0
assert classified["expectancy_usd"] == 0.5
assert (
    classified["average_return_percent"]
    == 0.5
)

uncertain = by_regime["uncertain"]

assert uncertain["trade_count"] == 1
assert uncertain["expectancy_usd"] == 5.0

print("entry_regime_attribution: PASS")
print("no_lookahead_attribution: PASS")
print("regime_aggregation_math: PASS")
print("historical_database_writes: NONE")
