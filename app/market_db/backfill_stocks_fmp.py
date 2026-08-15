import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import requests
from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import MarketAsset, PriceObservation


FMP_HISTORY_URL = (
    "https://financialmodelingprep.com/stable/"
    "historical-price-eod/full"
)

STOCK_SYMBOLS = [
    "SPY",
    "QQQ",
    "DIA",
    "TSLA",
    "AAPL",
    "NVDA",
]

BACKFILL_PROVIDER = "FMP Historical"
REQUEST_TIMEOUT_SECONDS = 30
START_DATE = "2000-01-01"


def _to_decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _get_api_key() -> str:
    api_key = os.getenv("FMP_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("FMP_API_KEY is not configured.")

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
) -> list[dict]:
    response = requests.get(
        FMP_HISTORY_URL,
        params={
            "symbol": symbol,
            "from": START_DATE,
            "apikey": api_key,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"{symbol}: FMP returned HTTP {response.status_code}."
        )

    payload = response.json()

    if isinstance(payload, dict):
        message = (
            payload.get("Error Message")
            or payload.get("message")
            or payload.get("error")
        )

        raise RuntimeError(
            f"{symbol}: {message or 'Unexpected FMP response.'}"
        )

    if not isinstance(payload, list) or not payload:
        raise RuntimeError(
            f"{symbol}: No historical price data returned."
        )

    return payload


def backfill_stock(
    symbol: str,
    api_key: str,
) -> dict:
    history = _fetch_daily_history(
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

        for row in history:
            date_text = row.get("date")
            closing_price = _to_decimal(
                row.get("close")
            )

            if not date_text:
                invalid += 1
                continue

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
        "points_received": len(history),
        "inserted": inserted,
        "duplicates_skipped": duplicates,
        "invalid_skipped": invalid,
    }


def backfill_all_stocks() -> dict:
    api_key = _get_api_key()
    results = []

    for index, symbol in enumerate(STOCK_SYMBOLS):
        try:
            result = backfill_stock(
                symbol=symbol,
                api_key=api_key,
            )
        except Exception as error:
            result = {
                "symbol": symbol,
                "status": "failed",
                "error": str(error),
                "inserted": 0,
                "duplicates_skipped": 0,
            }
        else:
            result["status"] = "success"

        results.append(result)

        if index < len(STOCK_SYMBOLS) - 1:
            time.sleep(1)

    return {
        "status": "success",
        "provider": BACKFILL_PROVIDER,
        "assets_processed": len(results),
        "assets_succeeded": sum(
            result["status"] == "success"
            for result in results
        ),
        "assets_failed": sum(
            result["status"] == "failed"
            for result in results
        ),
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
