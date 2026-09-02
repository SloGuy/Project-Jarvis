from datetime import (
    datetime,
    timedelta,
    timezone,
)

from app.capital.market_regime import (
    classify_market_regime,
    classify_market_regime_at,
)


def build_weekday_series(
    prices: list[float],
) -> list[dict]:
    current = datetime(
        2026,
        1,
        5,
        21,
        0,
        tzinfo=timezone.utc,
    )

    series = []

    for price in prices:
        while current.weekday() >= 5:
            current += timedelta(days=1)

        series.append(
            {
                "observed_at": (
                    current.isoformat()
                ),
                "price_usd": price,
            }
        )

        current += timedelta(days=1)

    return series


def fresh_now(
    series: list[dict],
) -> datetime:
    return (
        datetime.fromisoformat(
            series[-1]["observed_at"]
        )
        + timedelta(hours=1)
    )


bullish_series = build_weekday_series(
    [
        100.0 + index
        for index in range(20)
    ]
)

bullish = classify_market_regime(
    series=bullish_series,
    now=fresh_now(bullish_series),
)

assert bullish["status"] == "success"
assert bullish["trend_regime"] == "bullish"
assert bullish["evidence"]["label"] == "developing"
assert (
    bullish["evidence"][
        "classification_session_count"
    ]
    == 20
)

bearish_series = build_weekday_series(
    [
        120.0 - index
        for index in range(20)
    ]
)

bearish = classify_market_regime(
    series=bearish_series,
    now=fresh_now(bearish_series),
)

assert bearish["status"] == "success"
assert bearish["trend_regime"] == "bearish"

range_bound_series = build_weekday_series(
    [100.0] * 20
)

range_bound = classify_market_regime(
    series=range_bound_series,
    now=fresh_now(range_bound_series),
)

assert range_bound["status"] == "success"
assert (
    range_bound["trend_regime"]
    == "range_bound"
)
assert (
    range_bound["volatility_regime"]
    == "low"
)
assert (
    range_bound["combined_regime"]
    == "range_bound_low_volatility"
)

high_volatility_series = build_weekday_series(
    [
        110.0 if index % 2 == 0 else 100.0
        for index in range(20)
    ]
)

high_volatility = classify_market_regime(
    series=high_volatility_series,
    now=fresh_now(
        high_volatility_series
    ),
)

assert high_volatility["status"] == "success"
assert (
    high_volatility["volatility_regime"]
    == "high"
)

insufficient_series = build_weekday_series(
    [100.0] * 19
)

insufficient = classify_market_regime(
    series=insufficient_series,
    now=fresh_now(insufficient_series),
)

assert (
    insufficient["status"]
    == "insufficient_data"
)
assert (
    insufficient["combined_regime"]
    == "uncertain"
)
assert insufficient["metrics"] is None

stale_series = build_weekday_series(
    [100.0] * 20
)

stale = classify_market_regime(
    series=stale_series,
    now=(
        datetime.fromisoformat(
            stale_series[-1][
                "observed_at"
            ]
        )
        + timedelta(hours=97)
    ),
)

assert stale["status"] == "stale"
assert stale["combined_regime"] == "uncertain"
assert stale["metrics"] is None

for result in (
    bullish,
    bearish,
    range_bound,
    high_volatility,
    insufficient,
    stale,
):
    assert result["advisory_only"] is True
    assert (
        result["allocation_authority"]
        is False
    )
    assert (
        result["live_capital_authority"]
        is False
    )

historical = classify_market_regime_at(
    series=bullish_series,
    observed_at=datetime.fromisoformat(
        bullish_series[-2]["observed_at"]
    ),
)

assert (
    historical["status"]
    == "insufficient_data"
)
assert (
    historical["combined_regime"]
    == "uncertain"
)

print("bullish_classification: PASS")
print("bearish_classification: PASS")
print("range_bound_classification: PASS")
print("volatility_classification: PASS")
print("insufficient_data_fallback: PASS")
print("stale_data_fallback: PASS")
print("allocation_authority: NONE")
print("live_capital_authority: NONE")
