"""Provider-time provenance contracts, separate from validation receipts.

These records distinguish provider-reported time from local capture time.
They do not establish provider authenticity, feed completeness, or freshness.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation


PROVIDER_CONTRACTS = {
    "Finnhub REST": ("stock", "seconds"),
    "Finnhub WebSocket": ("stock", "milliseconds"),
    "CoinGecko REST": ("crypto", "seconds"),
}

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _aware_time(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Local capture time must include a timezone.")
    return value.astimezone(timezone.utc)


def _price(value):
    if isinstance(value, bool):
        raise ValueError("Price must be finite and positive.")
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("Invalid price.") from error
    if not price.is_finite() or price <= 0:
        raise ValueError("Price must be finite and positive.")
    return price


def make_quote_provenance(
    *,
    asset_id,
    symbol,
    asset_type,
    provider,
    price_usd,
    provider_timestamp,
    captured_at,
):
    """Build a record from a provider response and local processing time.

    provider_timestamp is the original integer Unix timestamp or None.
    captured_at is sampled locally while handling this response. It is
    not a network-arrival timestamp or a substitute for provider time.
    """
    if type(asset_id) is not int or asset_id <= 0:
        raise ValueError("asset_id must be a positive integer.")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be nonempty.")
    if not isinstance(provider, str) or provider not in PROVIDER_CONTRACTS:
        raise ValueError("Unsupported provider contract.")

    expected_type, unit = PROVIDER_CONTRACTS[provider]
    if asset_type != expected_type:
        raise ValueError("Asset type does not match the provider contract.")

    captured = _aware_time(captured_at)
    price = _price(price_usd)
    provider_at = None

    if provider_timestamp is not None:
        if type(provider_timestamp) is not int or provider_timestamp <= 0:
            raise ValueError("Provider timestamp must be a positive integer.")
        try:
            delta = (
                timedelta(seconds=provider_timestamp)
                if unit == "seconds"
                else timedelta(milliseconds=provider_timestamp)
            )
            provider_at = EPOCH + delta
        except OverflowError as error:
            raise ValueError("Provider timestamp is outside supported range.") from error

        if provider_at > captured:
            raise ValueError("Provider timestamp is after local capture time.")

    return {
        "schema_version": 1,
        "asset_id": asset_id,
        "symbol": symbol.strip().upper(),
        "asset_type": asset_type,
        "provider": provider,
        "price_usd": str(price),
        "provider_timestamp_raw": provider_timestamp,
        "provider_timestamp_unit": unit,
        "provider_observed_at": (
            provider_at.isoformat() if provider_at is not None else None
        ),
        "provider_time_status": (
            "reported" if provider_at is not None else "missing"
        ),
        "captured_at": captured.isoformat(),
        "capture_time_basis": "local_response_processing",
        "market_quote_freshness_verified": False,
        "provider_authenticity_verified": False,
        "feed_completeness_verified": False,
    }
