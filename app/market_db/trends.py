from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import MarketAsset, PriceObservation



def _downsample_points(
    points: list[dict],
    maximum_points: int,
) -> list[dict]:
    if len(points) <= maximum_points:
        return points

    step = (len(points) - 1) / (maximum_points - 1)

    indexes = {
        round(index * step)
        for index in range(maximum_points)
    }

    indexes.add(0)
    indexes.add(len(points) - 1)

    return [
        points[index]
        for index in sorted(indexes)
    ]


def _percent_change(
    first_price: Decimal,
    latest_price: Decimal,
) -> float | None:
    if first_price == 0:
        return None

    return float(
        ((latest_price - first_price) / first_price)
        * Decimal("100")
    )


def get_asset_trend(
    symbol: str,
    hours: int = 24,
    limit: int = 5000,
    all_history: bool = False,
    chart_points: int = 800,
) -> dict:
    normalized_symbol = symbol.upper().strip()

    cutoff = (
        None
        if all_history
        else datetime.now(timezone.utc) - timedelta(hours=hours)
    )

    with SessionLocal() as session:
        asset = session.scalar(
            select(MarketAsset).where(
                MarketAsset.symbol == normalized_symbol,
                MarketAsset.is_active.is_(True),
            )
        )

        if asset is None:
            return {
                "status": "not_found",
                "symbol": normalized_symbol,
                "message": (
                    f"{normalized_symbol} is not currently tracked."
                ),
                "points": [],
            }

        observation_statement = (
            select(PriceObservation)
            .where(PriceObservation.asset_id == asset.id)
            .order_by(PriceObservation.observed_at.asc())
            .limit(limit)
        )

        if cutoff is not None:
            observation_statement = observation_statement.where(
                PriceObservation.observed_at >= cutoff
            )

        observations = session.scalars(
            observation_statement
        ).all()

    points = [
        {
            "price_usd": float(observation.price_usd),
            "provider_change_percent": (
                float(observation.change_percent)
                if observation.change_percent is not None
                else None
            ),
            "observed_at": observation.observed_at.isoformat(),
        }
        for observation in observations
    ]

    original_point_count = len(points)

    chart_data = _downsample_points(
        points,
        maximum_points=chart_points,
    )

    if not observations:
        return {
            "status": "unavailable",
            "symbol": normalized_symbol,
            "asset_type": asset.asset_type,
            "hours": None if all_history else hours,
            "all_history": all_history,
            "returned": 0,
            "message": (
                "No observations exist inside the requested window."
            ),
            "statistics": None,
            "points": [],
        }

    first = observations[0]
    latest = observations[-1]

    available_seconds = (
        latest.observed_at - first.observed_at
    ).total_seconds()

    available_hours = available_seconds / 3600

    window_fully_covered = (
        all_history
        or available_hours >= hours * 0.95
    )

    prices = [
        observation.price_usd
        for observation in observations
    ]

    change_usd = latest.price_usd - first.price_usd
    change_percent = _percent_change(
        first.price_usd,
        latest.price_usd,
    )

    direction = "flat"

    if change_percent is not None:
        if change_percent > 0:
            direction = "up"
        elif change_percent < 0:
            direction = "down"

    return {
        "status": "healthy",
        "symbol": normalized_symbol,
        "asset_type": asset.asset_type,
        "hours": None if all_history else hours,
        "all_history": all_history,
        "requested_hours": None if all_history else hours,
        "available_hours": round(available_hours, 2),
        "available_days": round(available_hours / 24, 2),
        "window_fully_covered": window_fully_covered,
        "returned": len(chart_data),
        "original_point_count": original_point_count,
        "downsampled": len(chart_data) < original_point_count,
        "statistics": {
            "first_price_usd": float(first.price_usd),
            "latest_price_usd": float(latest.price_usd),
            "lowest_price_usd": float(min(prices)),
            "highest_price_usd": float(max(prices)),
            "change_usd": float(change_usd),
            "change_percent": change_percent,
            "direction": direction,
            "first_observed_at": first.observed_at.isoformat(),
            "latest_observed_at": latest.observed_at.isoformat(),
        },
        "points": chart_data,
    }
