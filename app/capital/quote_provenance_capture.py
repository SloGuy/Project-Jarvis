"""Capture one fresh REST response into separate provenance storage.

No trading state or legacy price observations are written.
This function does not schedule collection or certify quote freshness.
"""

from datetime import datetime, timezone

from app.capital.quote_provenance_adapter import (
    coingecko_rest_provenance,
    finnhub_rest_provenance,
)
from app.capital.quote_provenance_store import save_quote_provenance


def capture_quote_provenance(
    *,
    asset,
    directory,
    finnhub_api_key=None,
):
    """Capture one asset whose identity was read from the asset registry.

    Provider errors propagate to the caller. Callers must not print raw
    request exceptions because their URLs may contain API credentials.
    """
    if not isinstance(asset, dict):
        raise ValueError("Asset must be a dictionary.")
    asset_id = asset.get("id")
    symbol = asset.get("symbol")
    asset_type = asset.get("asset_type")

    if type(asset_id) is not int or asset_id <= 0:
        raise ValueError("Asset ID must be a positive integer.")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Asset symbol must be nonempty.")
    symbol = symbol.strip().upper()
    if asset_type not in ("stock", "crypto"):
        raise ValueError("Unsupported asset type.")

    # Import lazily: importing this module alone makes no provider request.
    from app.watchlist_quotes import (
        CRYPTO_PROVIDER_IDS,
        _fetch_crypto_quotes,
        _fetch_stock_quote,
    )

    if asset_type == "stock":
        if not isinstance(finnhub_api_key, str) or not finnhub_api_key.strip():
            raise ValueError("Finnhub credentials are required.")

        quote = _fetch_stock_quote(symbol, finnhub_api_key.strip())
        captured_at = datetime.now(timezone.utc)
        if not isinstance(quote, dict):
            raise ValueError("Unexpected stock response.")
        if quote.get("source") != "finnhub_snapshot":
            raise ValueError("Unexpected stock quote source.")

        record = finnhub_rest_provenance(
            asset_id=asset_id,
            symbol=symbol,
            quote={**quote, "available": True},
            captured_at=captured_at,
        )
    else:
        provider_id = asset.get("provider_id")
        expected_id = CRYPTO_PROVIDER_IDS.get(symbol)
        if (
            not isinstance(provider_id, str)
            or not provider_id
            or expected_id != provider_id
        ):
            raise ValueError("Crypto registry and fetcher identities differ.")

        quotes = _fetch_crypto_quotes([symbol])
        captured_at = datetime.now(timezone.utc)
        if not isinstance(quotes, dict):
            raise ValueError("Unexpected crypto response.")
        quote = quotes.get(symbol)
        if not isinstance(quote, dict):
            raise ValueError("Requested crypto quote is unavailable.")
        if quote.get("source") != "coingecko_snapshot":
            raise ValueError("Unexpected crypto quote source.")

        record = coingecko_rest_provenance(
            asset_id=asset_id,
            symbol=symbol,
            provider_id=provider_id,
            quote={
                **quote,
                "available": True,
                # The fetcher selected this response by its checked ID map.
                "id": expected_id,
                "last_updated_at": quote.get("quote_timestamp"),
            },
            captured_at=captured_at,
        )

    saved = save_quote_provenance(directory=directory, record=record)
    return {
        **saved,
        "asset_id": asset_id,
        "symbol": symbol,
        "provider": record["provider"],
        "provider_observed_at": record["provider_observed_at"],
        "provider_time_status": record["provider_time_status"],
        "captured_at": record["captured_at"],
        "market_quote_freshness_verified": False,
        "trading_state_writes": False,
    }
