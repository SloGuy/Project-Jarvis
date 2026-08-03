from datetime import datetime, timezone

from sqlalchemy import func, select

from app.market_db.alerts import get_recent_alerts
from app.market_db.database import SessionLocal
from app.market_db.models import (
    MarketAlert,
    MarketAsset,
    MarketNewsArticle,
    MarketNewsArticleAsset,
    PriceObservation,
)
from app.market_db.moves import get_latest_market_moves
from app.market_db.news_queries import get_recent_market_news


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

        alert_count = session.scalar(
            select(func.count(MarketAlert.id))
        ) or 0

        news_article_count = session.scalar(
            select(func.count(MarketNewsArticle.id))
        ) or 0

        processed_news_count = session.scalar(
            select(func.count(MarketNewsArticle.id)).where(
                MarketNewsArticle.processed.is_(True)
            )
        ) or 0

        news_asset_link_count = session.scalar(
            select(func.count(MarketNewsArticleAsset.id))
        ) or 0

        database_size_bytes = session.scalar(
            select(
                func.pg_database_size(
                    func.current_database()
                )
            )
        ) or 0

        first_observation = session.scalar(
            select(func.min(PriceObservation.observed_at))
        )

        latest_observation = session.scalar(
            select(func.max(PriceObservation.observed_at))
        )

        latest_news_published = session.scalar(
            select(func.max(MarketNewsArticle.published_at))
        )

        latest_news_fetched = session.scalar(
            select(func.max(MarketNewsArticle.fetched_at))
        )

        latest_alert = session.scalar(
            select(func.max(MarketAlert.created_at))
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
        "total_alerts": alert_count,
        "news_articles": news_article_count,
        "processed_news_articles": processed_news_count,
        "unprocessed_news_articles": (
            news_article_count - processed_news_count
        ),
        "news_asset_links": news_asset_link_count,
        "database_size_bytes": database_size_bytes,
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
        "latest_news_published_at": (
            latest_news_published.isoformat()
            if latest_news_published is not None
            else None
        ),
        "latest_news_fetched_at": (
            latest_news_fetched.isoformat()
            if latest_news_fetched is not None
            else None
        ),
        "latest_alert_at": (
            latest_alert.isoformat()
            if latest_alert is not None
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

    recent_news = get_recent_market_news(limit=10)

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

    insights = []

    if latest_age is not None and latest_age <= 1800:
        insights.append(
            "All market feeds are current."
        )
    elif latest_age is not None:
        insights.append(
            "Market data may be stale."
        )

    if database["news_articles"] == 0:
        insights.append(
            "No market news articles are currently stored."
        )
    elif database["unprocessed_news_articles"] > 0:
        insights.append(
            (
                f'{database["unprocessed_news_articles"]} market news '
                "articles are waiting to be processed."
            )
        )
    else:
        insights.append(
            "All stored market news articles have been processed."
        )

    if database["news_asset_links"] == 0:
        insights.append(
            "No market news articles are currently linked to tracked assets."
        )
    elif database["news_asset_links"] == 1:
        insights.append(
            "1 market news article is linked to a tracked asset."
        )
    else:
        insights.append(
            (
                f'{database["news_asset_links"]} market news links '
                "have been created for tracked assets."
            )
        )

    all_assets = stocks + crypto

    if database["total_alerts"] == 0:
        insights.append(
            "No threshold-based market alerts have been triggered."
        )
    elif database["total_alerts"] == 1:
        insights.append(
            "1 market alert has been recorded."
        )
    else:
        insights.append(
            f'{database["total_alerts"]} market alerts have been recorded.'
        )

    if all_assets:
        top_mover = max(
            all_assets,
            key=lambda asset: abs(
                asset["provider_change_percent"] or 0
            ),
        )

        top_move = top_mover["provider_change_percent"]

        if top_move is not None:
            direction = "up" if top_move > 0 else "down"

            insights.append(
                (
                    f'{top_mover["symbol"]} is the strongest current '
                    f'mover, {direction} {abs(top_move):.3f}%.'
                )
            )

    return {
        "status": status,
        "generated_at": generated_at.isoformat(),
        "summary": summary,
        "insights": insights,
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
        "recent_news": recent_news,
    }
