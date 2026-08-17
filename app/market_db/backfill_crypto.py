import json
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import requests
from sqlalchemy import select
from app.market_universe import (
    get_historical_crypto_provider_map,
)

from app.market_db.database import SessionLocal
from app.market_db.models import MarketAsset, PriceObservation


COINGECKO_MARKET_CHART_URL = (
    "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
)

CRYPTO_ASSETS = (
    get_historical_crypto_provider_map()
)

REQUEST_TIMEOUT_SECONDS = 30
BACKFILL_PROVIDER = "CoinGecko Historical"


def _to_decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _get_or_create_asset(
    session,
    *,
    coin_id: str,
    symbol: str,
) -> MarketAsset:
    asset = session.scalar(
        select(MarketAsset).where(
            MarketAsset.symbol == symbol,
            MarketAsset.asset_type == "crypto",
        )
    )

    if asset is not None:
        if not asset.provider_id:
            asset.provider_id = coin_id

        return asset

    asset = MarketAsset(
        symbol=symbol,
        asset_type="crypto",
        provider_id=coin_id,
        is_active=True,
    )

    session.add(asset)
    session.flush()

    return asset


def _fetch_history(coin_id: str, days: int = 365) -> list:
    response = requests.get(
        COINGECKO_MARKET_CHART_URL.format(coin_id=coin_id),
        params={
            "vs_currency": "usd",
            "days": str(days),
            "interval": "daily",
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    return response.json().get("prices", [])


def backfill_crypto_asset(
    coin_id: str,
    symbol: str,
    days: int = 365,
) -> dict:
    prices = _fetch_history(
        coin_id=coin_id,
        days=days,
    )

    inserted = 0
    duplicates = 0
    invalid = 0

    with SessionLocal() as session:
        asset = _get_or_create_asset(
            session,
            coin_id=coin_id,
            symbol=symbol,
        )

        existing_timestamps = set(
            session.scalars(
                select(PriceObservation.observed_at).where(
                    PriceObservation.asset_id == asset.id,
                    PriceObservation.provider == BACKFILL_PROVIDER,
                )
            ).all()
        )

        for timestamp_ms, raw_price in prices:
            price = _to_decimal(raw_price)

            if price is None or price <= 0:
                invalid += 1
                continue

            observed_at = datetime.fromtimestamp(
                timestamp_ms / 1000,
                tz=timezone.utc,
            )

            if observed_at in existing_timestamps:
                duplicates += 1
                continue

            session.add(
                PriceObservation(
                    asset_id=asset.id,
                    price_usd=price,
                    change_percent=None,
                    provider=BACKFILL_PROVIDER,
                    observed_at=observed_at,
                )
            )

            existing_timestamps.add(observed_at)
            inserted += 1

        session.commit()

    return {
        "symbol": symbol,
        "coin_id": coin_id,
        "points_received": len(prices),
        "inserted": inserted,
        "duplicates_skipped": duplicates,
        "invalid_skipped": invalid,
    }


def backfill_all_crypto(days: int = 365) -> dict:
    results = []

    for index, (coin_id, symbol) in enumerate(
        CRYPTO_ASSETS.items()
    ):
        results.append(
            backfill_crypto_asset(
                coin_id=coin_id,
                symbol=symbol,
                days=days,
            )
        )

        if index < len(CRYPTO_ASSETS) - 1:
            time.sleep(2)

    return {
        "status": "success",
        "days_requested": days,
        "assets_processed": len(results),
        "total_inserted": sum(
            result["inserted"]
            for result in results
        ),
        "total_duplicates_skipped": sum(
            result["duplicates_skipped"]
            for result in results
        ),
        "results": results,
    }


if __name__ == "__main__":
    print(
        json.dumps(
            backfill_all_crypto(days=365),
            indent=2,
        )
    )
