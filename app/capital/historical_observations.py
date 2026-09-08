"""Historical windows for engineering replay, not availability proof."""
from datetime import datetime, timezone

from sqlalchemy import select

from app.capital.mean_reversion_math import (
    LOOKBACK_OBSERVATIONS,
    calculate_mean_reversion_snapshot,
)
from app.market_db.models import MarketAsset, PriceObservation


COLLECTED_PROVIDERS = frozenset({"Finnhub", "CoinGecko"})


def historical_window_statement(
    *, asset_id: int, provider: str, decision_at: datetime
):
    if type(asset_id) is not int or asset_id < 1:
        raise ValueError("asset_id must be a positive integer.")
    if provider not in COLLECTED_PROVIDERS:
        raise ValueError("Choose an explicitly supported collected provider.")
    if not isinstance(decision_at, datetime) or decision_at.utcoffset() is None:
        raise ValueError("decision_at must include a timezone.")

    return (
        select(
            PriceObservation.id,
            PriceObservation.price_usd,
            PriceObservation.observed_at,
            PriceObservation.provider,
        )
        .where(
            PriceObservation.asset_id == asset_id,
            PriceObservation.provider == provider,
            PriceObservation.observed_at
            < decision_at.astimezone(timezone.utc),
        )
        .order_by(
            PriceObservation.observed_at.desc(),
            PriceObservation.id.desc(),
        )
        .limit(LOOKBACK_OBSERVATIONS)
    )


def load_historical_snapshot(
    session, *, asset_id: int, provider: str, decision_at: datetime
) -> dict:
    statement = historical_window_statement(
        asset_id=asset_id, provider=provider, decision_at=decision_at
    )
    with session.no_autoflush:
        asset = session.execute(
            select(MarketAsset.symbol, MarketAsset.asset_type)
            .where(MarketAsset.id == asset_id)
        ).one_or_none()
        if asset is None:
            raise ValueError(f"Unknown asset ID: {asset_id}")
        rows = session.execute(statement).all()

    observations = [
        {
            # Preserve the existing history adapter's numeric conversion.
            "price_usd": float(row.price_usd),
            "observed_at": row.observed_at.isoformat(),
        }
        for row in rows
    ]
    snapshot = calculate_mean_reversion_snapshot(
        symbol=asset.symbol, observations=observations
    )
    return {
        "snapshot": snapshot,
        "asset_id": asset_id,
        "asset_type": asset.asset_type,
        "provider": provider,
        "decision_at": decision_at.astimezone(timezone.utc).isoformat(),
        "observation_ids": [row.id for row in rows],
        "cutoff_rule": "observed_at strictly before decision_at",
        "availability_verified": False,
        "purpose": "engineering_replay",
    }
