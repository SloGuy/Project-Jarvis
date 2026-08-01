from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import MarketAsset, PriceObservation


def _to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _percent_change(
    previous_price: Decimal,
    latest_price: Decimal,
) -> float | None:
    if previous_price == 0:
        return None

    return float(
        ((latest_price - previous_price) / previous_price)
        * Decimal("100")
    )


def _classify_move(percent_change: float | None) -> str:
    if percent_change is None:
        return "unknown"

    absolute_move = abs(percent_change)

    if absolute_move >= 2.0:
        return "large"
    if absolute_move >= 0.75:
        return "moderate"
    if absolute_move >= 0.25:
        return "small"

    return "minimal"


def get_latest_market_moves(
    symbol: str | None = None,
    limit: int = 100,
    minimum_move_percent: float = 0.0,
    comparison_minutes: int = 15,
) -> dict:
    normalized_symbol = symbol.upper().strip() if symbol else None

    with SessionLocal() as session:
        asset_statement = (
            select(MarketAsset)
            .where(MarketAsset.is_active.is_(True))
            .order_by(MarketAsset.symbol)
        )

        if normalized_symbol:
            asset_statement = asset_statement.where(
                MarketAsset.symbol == normalized_symbol
            )

        assets = session.scalars(asset_statement).all()
        moves = []

        for asset in assets:
            latest = session.scalar(
                select(PriceObservation)
                .where(PriceObservation.asset_id == asset.id)
                .order_by(PriceObservation.observed_at.desc())
                .limit(1)
            )

            if latest is None:
                continue

            comparison_cutoff = (
                latest.observed_at
                - timedelta(minutes=comparison_minutes)
            )

            previous = session.scalar(
                select(PriceObservation)
                .where(
                    PriceObservation.asset_id == asset.id,
                    PriceObservation.observed_at <= comparison_cutoff,
                )
                .order_by(PriceObservation.observed_at.desc())
                .limit(1)
            )

            if previous is None:
                continue

            interval_change_percent = _percent_change(
                previous.price_usd,
                latest.price_usd,
            )

            if (
                interval_change_percent is not None
                and abs(interval_change_percent)
                < minimum_move_percent
            ):
                continue

            direction = "flat"

            if interval_change_percent is not None:
                if interval_change_percent > 0:
                    direction = "up"
                elif interval_change_percent < 0:
                    direction = "down"

            interval_seconds = (
                latest.observed_at - previous.observed_at
            ).total_seconds()

            moves.append(
                {
                    "symbol": asset.symbol,
                    "asset_type": asset.asset_type,
                    "latest_price_usd": _to_float(
                        latest.price_usd
                    ),
                    "previous_price_usd": _to_float(
                        previous.price_usd
                    ),
                    "price_change_usd": float(
                        latest.price_usd - previous.price_usd
                    ),
                    "interval_change_percent": (
                        interval_change_percent
                    ),
                    "provider_change_percent": _to_float(
                        latest.change_percent
                    ),
                    "direction": direction,
                    "move_size": _classify_move(
                        interval_change_percent
                    ),
                    "requested_comparison_minutes": (
                        comparison_minutes
                    ),
                    "actual_interval_seconds": interval_seconds,
                    "latest_observed_at": (
                        latest.observed_at.isoformat()
                    ),
                    "previous_observed_at": (
                        previous.observed_at.isoformat()
                    ),
                    "provider": latest.provider,
                }
            )

    moves.sort(
        key=lambda item: abs(
            item["interval_change_percent"] or 0.0
        ),
        reverse=True,
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": normalized_symbol,
        "comparison_minutes": comparison_minutes,
        "minimum_move_percent": minimum_move_percent,
        "returned": min(len(moves), limit),
        "moves": moves[:limit],
    }
