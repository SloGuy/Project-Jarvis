from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from app.live_market.live_state import get_live_quote
from app.market_db.models import MarketAsset, PriceObservation


MONEY_QUANTUM = Decimal("0.00000001")
ZERO = Decimal("0")

MAX_STOCK_PRICE_AGE_SECONDS = 20 * 60
MAX_CRYPTO_PRICE_AGE_SECONDS = 20 * 60

MAX_LIVE_STOCK_PRICE_AGE_SECONDS = 60
MAX_LIVE_CRYPTO_PRICE_AGE_SECONDS = 120


class MarketPricingError(ValueError):
    """Raised when a usable market price cannot be resolved."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _latest_asset_price(
    session: Any,
    asset_id: int,
) -> PriceObservation:
    observation = session.scalar(
        select(PriceObservation)
        .where(
            PriceObservation.asset_id == asset_id,
        )
        .order_by(
            PriceObservation.observed_at.desc(),
            PriceObservation.id.desc(),
        )
        .limit(1)
    )

    if observation is None:
        raise MarketPricingError(
            "No market price is available for this asset."
        )

    if observation.price_usd <= ZERO:
        raise MarketPricingError(
            "The latest market price is invalid."
        )

    return observation


def _validate_price_freshness(
    asset: MarketAsset,
    observation: PriceObservation,
) -> None:
    observed_at = observation.observed_at

    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(
            tzinfo=timezone.utc
        )

    age_seconds = (
        _utc_now() - observed_at
    ).total_seconds()

    if asset.asset_type == "stock":
        max_age_seconds = MAX_STOCK_PRICE_AGE_SECONDS
    elif asset.asset_type == "crypto":
        max_age_seconds = MAX_CRYPTO_PRICE_AGE_SECONDS
    else:
        raise MarketPricingError(
            f"Unsupported asset type: {asset.asset_type}."
        )

    if age_seconds < 0:
        raise MarketPricingError(
            f"Latest {asset.symbol} price timestamp "
            "is in the future."
        )

    if age_seconds > max_age_seconds:
        raise MarketPricingError(
            f"Latest {asset.symbol} price is stale. "
            f"Provider: {observation.provider}; "
            f"age: {age_seconds:.0f} seconds; "
            f"maximum allowed: {max_age_seconds} seconds."
        )


def _fresh_live_quote(
    asset: MarketAsset,
) -> dict[str, Any] | None:
    quote = get_live_quote(asset.symbol)

    if quote is None:
        return None

    price_value = quote.get("price_usd")
    observed_at_value = quote.get("observed_at")
    received_at_value = quote.get("received_at")

    if (
        price_value is None
        or observed_at_value is None
        or received_at_value is None
    ):
        return None

    try:
        price = Decimal(str(price_value))
    except (InvalidOperation, TypeError, ValueError):
        return None

    if not price.is_finite() or price <= ZERO:
        return None

    try:
        observed_at = datetime.fromisoformat(
            str(observed_at_value).replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError:
        return None

    try:
        received_at = datetime.fromisoformat(
            str(received_at_value).replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError:
        return None

    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(
            tzinfo=timezone.utc
        )

    if received_at.tzinfo is None:
        received_at = received_at.replace(
            tzinfo=timezone.utc
        )

    now = _utc_now()

    received_age_seconds = (
        now - received_at
    ).total_seconds()

    provider_age_seconds = (
        now - observed_at
    ).total_seconds()

    if asset.asset_type == "stock":
        max_received_age_seconds = (
            MAX_LIVE_STOCK_PRICE_AGE_SECONDS
        )
        max_provider_age_seconds = 60
    elif asset.asset_type == "crypto":
        max_received_age_seconds = (
            MAX_LIVE_CRYPTO_PRICE_AGE_SECONDS
        )
        max_provider_age_seconds = 5 * 60
    else:
        return None

    if (
        received_age_seconds < 0
        or received_age_seconds
        > max_received_age_seconds
    ):
        return None

    if (
        provider_age_seconds < 0
        or provider_age_seconds
        > max_provider_age_seconds
    ):
        return None

    return {
        "price_usd": price.quantize(
            MONEY_QUANTUM
        ),
        "provider": quote.get("provider"),
        "observed_at": observed_at,
        "received_at": received_at,
        "age_seconds": received_age_seconds,
        "provider_age_seconds": (
            provider_age_seconds
        ),
        "source": "live",
    }


def resolve_market_price(
    *,
    session: Any,
    asset: MarketAsset,
) -> dict[str, Any]:
    """
    Resolve the best currently usable price for an asset.

    Fresh live market data is preferred. If unavailable,
    the latest sufficiently fresh stored observation is used.

    This function reads market data only. It does not execute trades.
    """

    live_quote = _fresh_live_quote(asset)

    if live_quote is not None:
        return live_quote

    observation = _latest_asset_price(
        session=session,
        asset_id=asset.id,
    )

    _validate_price_freshness(
        asset=asset,
        observation=observation,
    )

    observed_at = observation.observed_at

    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(
            tzinfo=timezone.utc
        )

    return {
        "price_usd": observation.price_usd.quantize(
            MONEY_QUANTUM
        ),
        "provider": observation.provider,
        "observed_at": observed_at,
        "age_seconds": (
            _utc_now() - observed_at
        ).total_seconds(),
        "source": "database_fallback",
    }
