from datetime import (
    datetime,
    timedelta,
    timezone,
)
from math import sqrt
from statistics import mean, stdev
from typing import Any

from app.capital.benchmark import (
    BENCHMARK_PROVIDER,
    BENCHMARK_SYMBOL,
    get_benchmark_performance,
)


REGIME_METHODOLOGY_VERSION = (
    "spy_20_session_v1"
)

LOOKBACK_CALENDAR_DAYS = 90
MINIMUM_SESSION_COUNT = 20
SHORT_WINDOW_SESSIONS = 5
LONG_WINDOW_SESSIONS = 20
MAXIMUM_FRESHNESS_HOURS = 96.0
TRADING_DAYS_PER_YEAR = 252

TREND_RETURN_THRESHOLD_PERCENT = 1.0
MOVING_AVERAGE_THRESHOLD_PERCENT = 0.25

LOW_VOLATILITY_THRESHOLD_PERCENT = 12.0
HIGH_VOLATILITY_THRESHOLD_PERCENT = 25.0


def _normalize_datetime(
    value: datetime,
) -> datetime:
    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(timezone.utc)


def _parse_datetime(
    value: str,
) -> datetime:
    return _normalize_datetime(
        datetime.fromisoformat(value)
    )


def _weekday_series(
    series: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered = sorted(
        series,
        key=lambda point: point["observed_at"],
    )

    return [
        point
        for point in ordered
        if (
            _parse_datetime(
                point["observed_at"]
            ).weekday()
            < 5
            and float(point["price_usd"]) > 0
        )
    ]


def _evidence_label(
    session_count: int,
) -> str:
    if session_count < MINIMUM_SESSION_COUNT:
        return "insufficient"

    if session_count < 40:
        return "developing"

    return "substantial"


def _uncertain_result(
    *,
    status: str,
    reason: str,
    session_count: int,
    latest_observed_at: str | None,
    freshness_hours: float | None,
) -> dict[str, Any]:
    return {
        "status": status,
        "methodology_version": (
            REGIME_METHODOLOGY_VERSION
        ),
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "benchmark_provider": BENCHMARK_PROVIDER,
        "trend_regime": "uncertain",
        "volatility_regime": "uncertain",
        "combined_regime": "uncertain",
        "evidence": {
            "label": _evidence_label(
                session_count
            ),
            "available_session_count": (
                session_count
            ),
            "required_session_count": (
                MINIMUM_SESSION_COUNT
            ),
        },
        "freshness": {
            "latest_observed_at": (
                latest_observed_at
            ),
            "age_hours": freshness_hours,
            "maximum_age_hours": (
                MAXIMUM_FRESHNESS_HOURS
            ),
        },
        "metrics": None,
        "reason": reason,
        "advisory_only": True,
        "allocation_authority": False,
        "live_capital_authority": False,
    }


def classify_market_regime(
    *,
    series: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized_now = _normalize_datetime(
        now or datetime.now(timezone.utc)
    )

    weekday_series = _weekday_series(series)
    session_count = len(weekday_series)

    latest_observed_at = (
        weekday_series[-1]["observed_at"]
        if weekday_series
        else None
    )

    if session_count < MINIMUM_SESSION_COUNT:
        return _uncertain_result(
            status="insufficient_data",
            reason=(
                "At least 20 weekday SPY closes are "
                "required for regime classification."
            ),
            session_count=session_count,
            latest_observed_at=latest_observed_at,
            freshness_hours=None,
        )

    latest_datetime = _parse_datetime(
        latest_observed_at
    )

    freshness_hours = max(
        0.0,
        (
            normalized_now - latest_datetime
        ).total_seconds()
        / 3600,
    )

    if (
        freshness_hours
        > MAXIMUM_FRESHNESS_HOURS
    ):
        return _uncertain_result(
            status="stale",
            reason=(
                "The latest SPY observation is too old "
                "for regime classification."
            ),
            session_count=session_count,
            latest_observed_at=latest_observed_at,
            freshness_hours=round(
                freshness_hours,
                2,
            ),
        )

    window = weekday_series[
        -LONG_WINDOW_SESSIONS:
    ]

    prices = [
        float(point["price_usd"])
        for point in window
    ]

    short_average = mean(
        prices[-SHORT_WINDOW_SESSIONS:]
    )
    long_average = mean(prices)

    starting_price = prices[0]
    latest_price = prices[-1]

    window_return = (
        (
            latest_price - starting_price
        )
        / starting_price
        * 100
    )

    moving_average_spread = (
        (
            short_average - long_average
        )
        / long_average
        * 100
    )

    daily_returns = [
        (current - previous) / previous
        for previous, current in zip(
            prices,
            prices[1:],
        )
        if previous > 0
    ]

    annualized_volatility = (
        stdev(daily_returns)
        * sqrt(TRADING_DAYS_PER_YEAR)
        * 100
    )

    if (
        window_return
        >= TREND_RETURN_THRESHOLD_PERCENT
        and moving_average_spread
        >= MOVING_AVERAGE_THRESHOLD_PERCENT
    ):
        trend_regime = "bullish"
    elif (
        window_return
        <= -TREND_RETURN_THRESHOLD_PERCENT
        and moving_average_spread
        <= -MOVING_AVERAGE_THRESHOLD_PERCENT
    ):
        trend_regime = "bearish"
    else:
        trend_regime = "range_bound"

    if (
        annualized_volatility
        < LOW_VOLATILITY_THRESHOLD_PERCENT
    ):
        volatility_regime = "low"
    elif (
        annualized_volatility
        >= HIGH_VOLATILITY_THRESHOLD_PERCENT
    ):
        volatility_regime = "high"
    else:
        volatility_regime = "normal"

    return {
        "status": "success",
        "methodology_version": (
            REGIME_METHODOLOGY_VERSION
        ),
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "benchmark_provider": BENCHMARK_PROVIDER,
        "trend_regime": trend_regime,
        "volatility_regime": volatility_regime,
        "combined_regime": (
            f"{trend_regime}_"
            f"{volatility_regime}_volatility"
        ),
        "evidence": {
            "label": _evidence_label(
                session_count
            ),
            "available_session_count": (
                session_count
            ),
            "classification_session_count": (
                len(window)
            ),
            "required_session_count": (
                MINIMUM_SESSION_COUNT
            ),
        },
        "freshness": {
            "latest_observed_at": (
                latest_observed_at
            ),
            "age_hours": round(
                freshness_hours,
                2,
            ),
            "maximum_age_hours": (
                MAXIMUM_FRESHNESS_HOURS
            ),
        },
        "metrics": {
            "starting_price_usd": (
                starting_price
            ),
            "latest_price_usd": latest_price,
            "window_return_percent": round(
                window_return,
                4,
            ),
            "short_moving_average_usd": round(
                short_average,
                4,
            ),
            "long_moving_average_usd": round(
                long_average,
                4,
            ),
            "moving_average_spread_percent": (
                round(
                    moving_average_spread,
                    4,
                )
            ),
            "annualized_volatility_percent": (
                round(
                    annualized_volatility,
                    4,
                )
            ),
        },
        "thresholds": {
            "trend_return_percent": (
                TREND_RETURN_THRESHOLD_PERCENT
            ),
            "moving_average_spread_percent": (
                MOVING_AVERAGE_THRESHOLD_PERCENT
            ),
            "low_volatility_percent": (
                LOW_VOLATILITY_THRESHOLD_PERCENT
            ),
            "high_volatility_percent": (
                HIGH_VOLATILITY_THRESHOLD_PERCENT
            ),
        },
        "reason": (
            "Regime classified from fresh SPY "
            "weekday closing observations."
        ),
        "advisory_only": True,
        "allocation_authority": False,
        "live_capital_authority": False,
    }


def get_market_regime(
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized_now = _normalize_datetime(
        now or datetime.now(timezone.utc)
    )

    benchmark = get_benchmark_performance(
        started_at=(
            normalized_now
            - timedelta(
                days=LOOKBACK_CALENDAR_DAYS
            )
        )
    )

    if benchmark.get("status") != "success":
        return _uncertain_result(
            status="unavailable",
            reason=benchmark.get(
                "reason",
                "SPY benchmark data is unavailable.",
            ),
            session_count=0,
            latest_observed_at=None,
            freshness_hours=None,
        )

    return classify_market_regime(
        series=benchmark["series"],
        now=normalized_now,
    )
