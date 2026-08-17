from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market_db.database import SessionLocal
from app.market_db.models import MarketAsset, PriceObservation


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None

    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _get_or_create_asset(
    session: Session,
    *,
    symbol: str,
    asset_type: str,
    provider_id: str | None = None,
) -> MarketAsset:
    statement = select(MarketAsset).where(
        MarketAsset.symbol == symbol.upper(),
        MarketAsset.asset_type == asset_type,
    )

    asset = session.scalar(statement)

    if asset is not None:
        asset.is_active = True

        if (
            provider_id
            and not asset.provider_id
        ):
            asset.provider_id = provider_id

        return asset

    asset = MarketAsset(
        symbol=symbol.upper(),
        asset_type=asset_type,
        provider_id=provider_id,
        is_active=True,
    )

    session.add(asset)
    session.flush()

    return asset


def _record_observation(
    session: Session,
    *,
    symbol: str,
    asset_type: str,
    price: Any,
    change_percent: Any,
    provider: str,
    provider_id: str | None = None,
) -> bool:
    price_decimal = _to_decimal(price)

    if price_decimal is None or price_decimal <= 0:
        return False

    asset = _get_or_create_asset(
        session,
        symbol=symbol,
        asset_type=asset_type,
        provider_id=provider_id,
    )

    observation = PriceObservation(
        asset_id=asset.id,
        price_usd=price_decimal,
        change_percent=_to_decimal(change_percent),
        provider=provider,
    )

    session.add(observation)

    return True


def record_market_snapshot(snapshot: dict) -> dict:
    recorded_stocks = 0
    recorded_crypto = 0
    skipped = 0

    with SessionLocal() as session:
        try:
            stock_section = snapshot.get("stocks", {})
            stock_provider = stock_section.get("provider", "unknown")

            for quote in stock_section.get("quotes", []):
                if not quote.get("available", False):
                    skipped += 1
                    continue

                recorded = _record_observation(
                    session,
                    symbol=quote.get("symbol", ""),
                    asset_type="stock",
                    price=quote.get("price_usd"),
                    change_percent=quote.get("change_percent"),
                    provider=stock_provider,
                )

                if recorded:
                    recorded_stocks += 1
                else:
                    skipped += 1

            crypto_section = snapshot.get("crypto", {})
            crypto_provider = crypto_section.get("provider", "unknown")

            for quote in crypto_section.get("assets", []):
                if not quote.get("available", False):
                    skipped += 1
                    continue

                recorded = _record_observation(
                    session,
                    symbol=quote.get("symbol", ""),
                    asset_type="crypto",
                    price=quote.get("price_usd"),
                    change_percent=quote.get("change_24h_percent"),
                    provider=crypto_provider,
                    provider_id=quote.get("id"),
                )

                if recorded:
                    recorded_crypto += 1
                else:
                    skipped += 1

            session.commit()

        except Exception:
            session.rollback()
            raise

    return {
        "recorded": recorded_stocks + recorded_crypto,
        "stocks_recorded": recorded_stocks,
        "crypto_recorded": recorded_crypto,
        "skipped": skipped,
    }
