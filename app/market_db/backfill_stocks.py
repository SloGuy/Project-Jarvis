import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import requests
from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import MarketAsset, PriceObservation


ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"

STOCK_SYMBOLS = [
    "SPY",
    "QQQ",
    "DIA",
    "TSLA",
    "AAPL",
    "NVDA",
]

BACKFILL_PROVIDER = "Alpha Vantage Historical"
REQUEST_TIMEOUT_SECONDS = 30


def _to_decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _get_api_key() -> str:
    api_key = os.getenv(
        "ALPHA_VANTAGE_API_KEY",
        "",
    ).strip()

    if not api_key:
        raise RuntimeError(
            "ALPHA_VANTAGE_API_KEY is not configured."
        )

    return api_key


def _get_or_create_asset(
    session,
    symbol: str,
) -> MarketAsset:
    asset = session.scalar(
        select(MarketAsset).where(
            MarketAsset.symbol == symbol,
            MarketAsset.asset_type == "stock",
        )
    )

    if asset is not None:
        return asset

    asset = MarketAsset(
        symbol=symbol,
        asset_type="stock",
        provider_id=symbol,
        is_active=True,
    )

    session.add(asset)
    session.flush()

    return asset


def _fetch_daily_history(
    symbol: str,
    api_key: str,
) -> dict:
    response = requests.get(
        ALPHA_VANTAGE_URL,
        params={
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": "compact",
            "datatype": "json",
            "apikey": api_key,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()
    payload = response.json()

    error_message = (
        payload.get("Error Message")
        or payload.get("Information")
        or payload.get("Note")
    )

    if error_message:
        raise RuntimeError(
            f"{symbol}: {error_message}"
        )

    time_series = payload.get(
        "Time Series (Daily)",
        {},
    )

    if not time_series:
        raise RuntimeError(
            f"{symbol}: No daily time-series data returned."
        )

    return time_series


def backfill_stock(
    symbol: str,
    api_key: str,
) -> dict:
    time_series = _fetch_daily_history(
        symbol=symbol,
        api_key=api_key,
    )

    inserted = 0
    duplicates = 0
    invalid = 0

    with SessionLocal() as session:
        asset = _get_or_create_asset(
            session=session,
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

        for date_text, daily_data in time_series.items():
            closing_price = _to_decimal(
                daily_data.get("4. close")
            )

            if closing_price is None or closing_price <= 0:
                invalid += 1
                continue

            observed_at = datetime.strptime(
                date_text,
                "%Y-%m-%d",
            ).replace(
                hour=20,
                minute=0,
                second=0,
                tzinfo=timezone.utc,
            )

            if observed_at in existing_timestamps:
                duplicates += 1
                continue

            session.add(
                PriceObservation(
                    asset_id=asset.id,
                    price_usd=closing_price,
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
        "points_received": len(time_series),
        "inserted": inserted,
        "duplicates_skipped": duplicates,
        "invalid_skipped": invalid,
    }


def backfill_all_stocks() -> dict:
    api_key = _get_api_key()
    results = []

    for index, symbol in enumerate(STOCK_SYMBOLS):
        results.append(
            backfill_stock(
                symbol=symbol,
                api_key=api_key,
            )
        )

        if index < len(STOCK_SYMBOLS) - 1:
            time.sleep(3)

    return {
        "status": "success",
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
            backfill_all_stocks(),
            indent=2,
        )
    )
