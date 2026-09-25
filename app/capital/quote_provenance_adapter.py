"""Convert individual REST quote results into provenance records.

Call immediately after receiving and parsing the individual response.
Do not use cached snapshots or substitute snapshot-wide checked_at values
for the local response-processing timestamp.
"""

from app.capital.quote_provenance import make_quote_provenance


def _identity(*, asset_id, symbol, quote):
    if type(asset_id) is not int or asset_id <= 0:
        raise ValueError("asset_id must be a positive integer.")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Expected symbol must be nonempty.")
    if not isinstance(quote, dict):
        raise ValueError("Quote must be a dictionary.")
    if quote.get("available") is not True:
        raise ValueError("Quote is not explicitly available.")

    actual_symbol = quote.get("symbol")
    if not isinstance(actual_symbol, str):
        raise ValueError("Quote symbol is missing.")
    normalized = symbol.strip().upper()
    if actual_symbol.strip().upper() != normalized:
        raise ValueError("Quote symbol does not match the requested asset.")
    return normalized


def finnhub_rest_provenance(
    *,
    asset_id,
    symbol,
    quote,
    captured_at,
):
    """Adapt an individual _get_stock_quote result.

    quote_timestamp is preserved as provider time, including None.
    No alternative timestamp field is used as a fallback.
    """
    normalized = _identity(
        asset_id=asset_id,
        symbol=symbol,
        quote=quote,
    )
    return make_quote_provenance(
        asset_id=asset_id,
        symbol=normalized,
        asset_type="stock",
        provider="Finnhub REST",
        price_usd=quote.get("price_usd"),
        provider_timestamp=quote.get("quote_timestamp"),
        captured_at=captured_at,
    )


def coingecko_rest_provenance(
    *,
    asset_id,
    symbol,
    provider_id,
    quote,
    captured_at,
):
    """Adapt one asset from an individual CoinGecko response.

    The expected CoinGecko ID must match as well as the symbol.
    All assets in one response may share a local processing timestamp.
    """
    normalized = _identity(
        asset_id=asset_id,
        symbol=symbol,
        quote=quote,
    )
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise ValueError("Expected CoinGecko provider ID must be nonempty.")
    if quote.get("id") != provider_id.strip():
        raise ValueError("CoinGecko ID does not match the requested asset.")

    return make_quote_provenance(
        asset_id=asset_id,
        symbol=normalized,
        asset_type="crypto",
        provider="CoinGecko REST",
        price_usd=quote.get("price_usd"),
        provider_timestamp=quote.get("last_updated_at"),
        captured_at=captured_at,
    )
