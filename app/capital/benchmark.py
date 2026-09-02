from datetime import datetime, timezone
from math import sqrt
from statistics import stdev
from typing import Any

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import (
    MarketAsset,
    PriceObservation,
)


BENCHMARK_SYMBOL = "SPY"
BENCHMARK_PROVIDER = "Finnhub"
TRADING_DAYS_PER_YEAR = 252


def _normalize_datetime(
    value: datetime,
) -> datetime:
    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


def _maximum_drawdown(
    values: list[float],
) -> float | None:
    if not values:
        return None

    peak = values[0]
    maximum_drawdown = 0.0

    for value in values:
        peak = max(peak, value)

        if peak > 0:
            drawdown = (
                (peak - value)
                / peak
                * 100
            )

            maximum_drawdown = max(
                maximum_drawdown,
                drawdown,
            )

    return maximum_drawdown


def get_benchmark_performance(
    *,
    started_at: datetime,
) -> dict[str, Any]:
    normalized_start = _normalize_datetime(
        started_at
    )

    with SessionLocal() as session:
        asset = session.scalar(
            select(MarketAsset).where(
                MarketAsset.symbol
                == BENCHMARK_SYMBOL
            )
        )

        if asset is None:
            raise RuntimeError(
                f"{BENCHMARK_SYMBOL} was not found."
            )

        observations = session.scalars(
            select(PriceObservation)
            .where(
                PriceObservation.asset_id
                == asset.id,
                PriceObservation.provider
                == BENCHMARK_PROVIDER,
                PriceObservation.observed_at
                >= normalized_start,
            )
            .order_by(
                PriceObservation.observed_at.asc(),
                PriceObservation.id.asc(),
            )
        ).all()

    if not observations:
        return {
            "status": "unavailable",
            "symbol": BENCHMARK_SYMBOL,
            "provider": BENCHMARK_PROVIDER,
            "started_at": normalized_start.isoformat(),
            "reason": (
                "No benchmark observations are available."
            ),
        }

    daily_closes: dict[str, dict[str, Any]] = {}

    for observation in observations:
        observed_at = _normalize_datetime(
            observation.observed_at
        )

        date_key = observed_at.date().isoformat()

        daily_closes[date_key] = {
            "date": date_key,
            "observed_at": observed_at.isoformat(),
            "price_usd": float(
                observation.price_usd
            ),
        }

    series = list(daily_closes.values())

    starting_price = float(
        observations[0].price_usd
    )

    current_price = float(
        observations[-1].price_usd
    )

    total_return = (
        (
            current_price
            - starting_price
        )
        / starting_price
        * 100
        if starting_price > 0
        else None
    )

    daily_returns = []

    for previous, current in zip(
        series,
        series[1:],
    ):
        previous_price = float(
            previous["price_usd"]
        )

        current_price_value = float(
            current["price_usd"]
        )

        if previous_price > 0:
            daily_returns.append(
                (
                    current_price_value
                    - previous_price
                )
                / previous_price
            )

    annualized_volatility = (
        stdev(daily_returns)
        * sqrt(TRADING_DAYS_PER_YEAR)
        * 100
        if len(daily_returns) >= 2
        else None
    )

    return {
        "status": "success",
        "symbol": BENCHMARK_SYMBOL,
        "provider": BENCHMARK_PROVIDER,
        "started_at": normalized_start.isoformat(),
        "first_observed_at": (
            observations[0]
            .observed_at
            .isoformat()
        ),
        "latest_observed_at": (
            observations[-1]
            .observed_at
            .isoformat()
        ),
        "starting_price_usd": starting_price,
        "current_price_usd": current_price,
        "total_return_percent": total_return,
        "annualized_volatility_percent": (
            annualized_volatility
        ),
        "maximum_drawdown_percent": (
            _maximum_drawdown(
                [
                    float(item["price_usd"])
                    for item in series
                ]
            )
        ),
        "daily_observation_count": len(series),
        "daily_returns": daily_returns,
        "series": series,
    }
