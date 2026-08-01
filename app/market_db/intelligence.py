from datetime import datetime, timezone

from sqlalchemy import func, select

from app.market_db.alerts import get_recent_alerts
from app.market_db.database import SessionLocal
from app.market_db.models import MarketAsset, PriceObservation
from app.market_db.moves import get_latest_market_moves


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _latest_assets() -> list[dict]:
    with SessionLocal() as session:
        assets = session.scalars(
            select(MarketAsset)
            .where(MarketAsset.is_active.is_(True))
            .order_by(MarketAsset.asset_type, MarketAsset.symbol)
        ).all()

        latest_assets = []

        for asset in assets:
            observation = session.scalar(
                select(PriceObservation)
                .where(PriceObservation.asset_id == asset.id)
                .order_by(PriceObservation.observed_at.desc())
                .limit(1)
            )

            if observation is None:
                continue

            latest_assets.append(
                {
                    "symbol": asset.symbol,
                    "asset_type": asset.asset_type,
                    "price_usd": float(observation.price_usd),
                    "provider_change_percent": (
                        float(observation.change_percent)
                        if observation.change_percent is not None
                        else None
                    ),
                    "provider": observation.provider,
                    "observed_at": observation.observed_at.isoformat(),
                }
            )

    return latest_assets


def _database_statistics() -> dict:
    with SessionLocal() as session:
        observation_count = session.scalar(
            select(func.count(PriceObservation.id))
        ) or 0

        asset_count = session.scalar(
            select(func.count(MarketAsset.id)).where(
                MarketAsset.is_active.is_(True)
            )
        ) or 0

        first_observation = session.scalar(
            select(func.min(PriceObservation.observed_at))
        )

        latest_observation = session.scalar(
            select(func.max(PriceObservation.observed_at))
        )

    latest_age_seconds = None

    if latest_observation is not None:
        latest_age_seconds = max(
            0.0,
            (_utc_now() - latest_observation).total_seconds(),
        )

    return {
        "active_assets": asset_count,
        "total_observations": observation_count,
        "first_observation_at": (
            first_observation.isoformat()
            if first_observation is not None
            else None
        ),
        "latest_observation_at": (
            latest_observation.isoformat()
            if latest_observation is not None
            else None
        ),
        "latest_observation_age_seconds": (
            round(latest_age_seconds, 1)
            if latest_age_seconds is not None
            else None
        ),
    }


def get_market_intelligence(
    comparison_minutes: int = 15,
    mover_threshold_percent: float = 0.25,
    alert_limit: int = 20,
) -> dict:
    generated_at = _utc_now()
    database = _database_statistics()
    assets = _latest_assets()

    moves = get_latest_market_moves(
        comparison_minutes=comparison_minutes,
        minimum_move_percent=mover_threshold_percent,
        limit=20,
    )

    alerts = get_recent_alerts(limit=alert_limit)

    latest_age = database["latest_observation_age_seconds"]

    if latest_age is None:
        status = "unavailable"
        summary = "No market observations have been stored."
    elif latest_age > 1800:
        status = "warning"
        summary = "Market intelligence data may be stale."
    else:
        status = "healthy"
        summary = (
            f'Jarvis is tracking {database["active_assets"]} assets '
            f'with {database["total_observations"]} stored observations.'
        )

    stocks = [
        asset for asset in assets
        if asset["asset_type"] == "stock"
    ]

    crypto = [
        asset for asset in assets
        if asset["asset_type"] == "crypto"
    ]

    return {
        "status": status,
        "generated_at": generated_at.isoformat(),
        "summary": summary,
        "comparison_minutes": comparison_minutes,
        "mover_threshold_percent": mover_threshold_percent,
        "database": database,
        "market": {
            "stocks": stocks,
            "crypto": crypto,
        },
        "movers": {
            "count": moves["returned"],
            "assets": moves["moves"],
        },
        "alerts": alerts,
    }
