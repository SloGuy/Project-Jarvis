from sqlalchemy import func, select

from app.market_db.database import SessionLocal
from app.market_db.models import MarketAsset, PriceObservation


def get_market_history(
    symbol: str | None = None,
    limit: int = 100,
) -> dict:
    normalized_symbol = symbol.upper().strip() if symbol else None

    with SessionLocal() as session:
        statement = (
            select(PriceObservation, MarketAsset)
            .join(
                MarketAsset,
                MarketAsset.id == PriceObservation.asset_id,
            )
            .order_by(PriceObservation.observed_at.desc())
            .limit(limit)
        )

        if normalized_symbol:
            statement = statement.where(
                MarketAsset.symbol == normalized_symbol
            )

        rows = session.execute(statement).all()

        total_statement = (
            select(func.count(PriceObservation.id))
            .join(
                MarketAsset,
                MarketAsset.id == PriceObservation.asset_id,
            )
        )

        if normalized_symbol:
            total_statement = total_statement.where(
                MarketAsset.symbol == normalized_symbol
            )

        total_observations = session.scalar(total_statement) or 0

    observations = []

    for observation, asset in rows:
        observations.append(
            {
                "symbol": asset.symbol,
                "asset_type": asset.asset_type,
                "price_usd": float(observation.price_usd),
                "change_percent": (
                    float(observation.change_percent)
                    if observation.change_percent is not None
                    else None
                ),
                "provider": observation.provider,
                "observed_at": observation.observed_at.isoformat(),
            }
        )

    return {
        "symbol": normalized_symbol,
        "returned": len(observations),
        "total_matching_observations": total_observations,
        "observations": observations,
    }
