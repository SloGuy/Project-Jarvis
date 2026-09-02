from datetime import (
    datetime,
    time,
    timedelta,
    timezone,
)
from math import sqrt
from statistics import stdev
from typing import Any

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import (
    PortfolioTransaction,
    PriceObservation,
)


CALENDAR_DAYS_PER_YEAR = 365


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


def _measurement_times(
    *,
    started_at: datetime,
    now: datetime,
) -> list[datetime]:
    times = [started_at]

    current_date = started_at.date()

    while current_date <= now.date():
        end_of_day = datetime.combine(
            current_date,
            time.max,
            tzinfo=timezone.utc,
        )

        if (
            end_of_day > started_at
            and end_of_day < now
        ):
            times.append(end_of_day)

        current_date += timedelta(days=1)

    if times[-1] != now:
        times.append(now)

    return times


def _latest_price(
    *,
    session: Any,
    asset_id: int,
    measured_at: datetime,
    fallback_price: float,
) -> float:
    price = session.scalar(
        select(PriceObservation.price_usd)
        .where(
            PriceObservation.asset_id
            == asset_id,
            PriceObservation.observed_at
            <= measured_at,
        )
        .order_by(
            PriceObservation.observed_at.desc(),
            PriceObservation.id.desc(),
        )
        .limit(1)
    )

    if price is None:
        return fallback_price

    return float(price)


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


def get_portfolio_equity_curve(
    *,
    portfolio_id: int,
    started_at: datetime,
    starting_capital_usd: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized_start = _normalize_datetime(
        started_at
    )

    normalized_now = _normalize_datetime(
        now or datetime.now(timezone.utc)
    )

    measurement_times = _measurement_times(
        started_at=normalized_start,
        now=normalized_now,
    )

    with SessionLocal() as session:
        transactions = session.scalars(
            select(PortfolioTransaction)
            .where(
                PortfolioTransaction.portfolio_id
                == portfolio_id,
                PortfolioTransaction.created_at
                >= normalized_start,
                PortfolioTransaction.created_at
                <= normalized_now,
            )
            .order_by(
                PortfolioTransaction.created_at.asc(),
                PortfolioTransaction.id.asc(),
            )
        ).all()

        cash = float(starting_capital_usd)
        quantities: dict[int, float] = {}
        fallback_prices: dict[int, float] = {}
        transaction_index = 0
        series: list[dict[str, Any]] = []

        for measured_at in measurement_times:
            while (
                transaction_index < len(transactions)
                and _normalize_datetime(
                    transactions[
                        transaction_index
                    ].created_at
                )
                <= measured_at
            ):
                transaction = transactions[
                    transaction_index
                ]

                transaction_type = (
                    transaction.transaction_type
                )

                asset_id = transaction.asset_id

                total_usd = float(
                    transaction.total_usd
                )

                fees_usd = float(
                    transaction.fees_usd
                    or 0
                )

                quantity = float(
                    transaction.quantity
                )

                if transaction_type == "buy":
                    cash -= total_usd + fees_usd

                    if asset_id is not None:
                        quantities[asset_id] = (
                            quantities.get(
                                asset_id,
                                0.0,
                            )
                            + quantity
                        )

                elif transaction_type == "sell":
                    cash += total_usd - fees_usd

                    if asset_id is not None:
                        quantities[asset_id] = (
                            quantities.get(
                                asset_id,
                                0.0,
                            )
                            - quantity
                        )

                if asset_id is not None:
                    fallback_prices[asset_id] = float(
                        transaction.price_usd
                    )

                transaction_index += 1

            market_value = 0.0

            for asset_id, quantity in quantities.items():
                if quantity <= 0:
                    continue

                price = _latest_price(
                    session=session,
                    asset_id=asset_id,
                    measured_at=measured_at,
                    fallback_price=(
                        fallback_prices[asset_id]
                    ),
                )

                market_value += quantity * price

            total_value = cash + market_value

            series.append(
                {
                    "measured_at": (
                        measured_at.isoformat()
                    ),
                    "cash_balance_usd": cash,
                    "market_value_usd": market_value,
                    "total_value_usd": total_value,
                }
            )

    daily_returns: list[float] = []

    for previous, current in zip(
        series,
        series[1:],
    ):
        previous_value = float(
            previous["total_value_usd"]
        )

        current_value = float(
            current["total_value_usd"]
        )

        if previous_value > 0:
            daily_returns.append(
                (
                    current_value
                    - previous_value
                )
                / previous_value
            )

    annualized_volatility = (
        stdev(daily_returns)
        * sqrt(CALENDAR_DAYS_PER_YEAR)
        * 100
        if len(daily_returns) >= 2
        else None
    )

    average_daily_return = (
        sum(daily_returns)
        / len(daily_returns)
        if daily_returns
        else None
    )

    daily_standard_deviation = (
        stdev(daily_returns)
        if len(daily_returns) >= 2
        else None
    )

    sharpe_ratio = (
        average_daily_return
        / daily_standard_deviation
        * sqrt(CALENDAR_DAYS_PER_YEAR)
        if (
            average_daily_return is not None
            and daily_standard_deviation
            not in (None, 0)
        )
        else None
    )

    downside_returns = [
        daily_return
        for daily_return in daily_returns
        if daily_return < 0
    ]

    downside_deviation = (
        sqrt(
            sum(
                value * value
                for value in downside_returns
            )
            / len(downside_returns)
        )
        if downside_returns
        else None
    )

    sortino_ratio = (
        average_daily_return
        / downside_deviation
        * sqrt(CALENDAR_DAYS_PER_YEAR)
        if (
            average_daily_return is not None
            and downside_deviation
            not in (None, 0)
        )
        else None
    )

    return {
        "status": "success",
        "portfolio_id": portfolio_id,
        "started_at": normalized_start.isoformat(),
        "generated_at": normalized_now.isoformat(),
        "observation_count": len(series),
        "return_observation_count": len(
            daily_returns
        ),
        "annualized_volatility_percent": (
            annualized_volatility
        ),
        "sharpe_ratio_zero_rate": sharpe_ratio,
        "sortino_ratio_zero_rate": sortino_ratio,
        "maximum_drawdown_percent": (
            _maximum_drawdown(
                [
                    float(item["total_value_usd"])
                    for item in series
                ]
            )
        ),
        "daily_returns": daily_returns,
        "series": series,
    }
