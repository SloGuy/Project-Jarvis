from datetime import datetime, timezone
import os
import time
from typing import Any

import requests

from app.market_universe import (
    get_crypto_provider_map,
    get_deep_snapshot_stock_symbols,
)


ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
ALPHA_VANTAGE_MARKET_STATUS_FUNCTION = "MARKET_STATUS"
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"

DEFAULT_STOCKS = list(
    get_deep_snapshot_stock_symbols()
)

DEFAULT_CRYPTO = (
    get_crypto_provider_map()
)

REQUEST_TIMEOUT_SECONDS = 10

CACHE_TTL_SECONDS = 900  # 15 minutes

_market_cache = None
_market_cache_timestamp = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_stock_quote(symbol: str, api_key: str) -> dict:
    try:
        response = requests.get(
            FINNHUB_QUOTE_URL,
            params={
                "symbol": symbol,
                "token": api_key,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()

        current_price = _safe_float(payload.get("c"))
        previous_close = _safe_float(payload.get("pc"))
        change = _safe_float(payload.get("d"))
        change_percent = _safe_float(payload.get("dp"))
        quote_timestamp = payload.get("t")

        if current_price is None or current_price <= 0:
            return {
                "symbol": symbol,
                "available": False,
                "error": "Finnhub returned no valid quote data.",
            }

        return {
            "symbol": symbol,
            "available": True,
            "price_usd": current_price,
            "previous_close_usd": previous_close,
            "change_usd": change,
            "change_percent": change_percent,
            "day_open_usd": _safe_float(payload.get("o")),
            "day_high_usd": _safe_float(payload.get("h")),
            "day_low_usd": _safe_float(payload.get("l")),
            "quote_timestamp": quote_timestamp,
        }

    except requests.RequestException as error:
        return {
            "symbol": symbol,
            "available": False,
            "error": str(error),
        }

    except ValueError as error:
        return {
            "symbol": symbol,
            "available": False,
            "error": f"Invalid JSON response: {error}",
        }


def _get_market_session_status(api_key: str) -> dict:
    try:
        response = requests.get(
            ALPHA_VANTAGE_URL,
            params={
                "function": ALPHA_VANTAGE_MARKET_STATUS_FUNCTION,
                "apikey": api_key,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()

        markets = payload.get("markets", [])

        if not markets:
            message = (
                payload.get("Information")
                or payload.get("Note")
                or payload.get("Error Message")
                or "No market-session data returned."
            )

            return {
                "available": False,
                "us_equity": None,
                "error": message,
            }

        us_equity = next(
            (
                market
                for market in markets
                if market.get("market_type") == "Equity"
                and "United States" in market.get("region", "")
            ),
            None,
        )

        if us_equity is None:
            return {
                "available": False,
                "us_equity": None,
                "error": "United States equity market status was not found.",
            }

        return {
            "available": True,
            "us_equity": {
                "market_type": us_equity.get("market_type"),
                "region": us_equity.get("region"),
                "primary_exchanges": us_equity.get("primary_exchanges"),
                "local_open": us_equity.get("local_open"),
                "local_close": us_equity.get("local_close"),
                "current_status": us_equity.get("current_status"),
                "notes": us_equity.get("notes"),
            },
            "error": None,
        }

    except requests.RequestException as error:
        return {
            "available": False,
            "us_equity": None,
            "error": str(error),
        }

    except ValueError as error:
        return {
            "available": False,
            "us_equity": None,
            "error": f"Invalid JSON response: {error}",
        }



def _get_crypto_quotes() -> dict:
    try:
        response = requests.get(
            COINGECKO_URL,
            params={
                "ids": ",".join(DEFAULT_CRYPTO.keys()),
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_last_updated_at": "true",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()

        assets = []

        for coin_id, symbol in DEFAULT_CRYPTO.items():
            coin = payload.get(coin_id, {})

            assets.append(
                {
                    "id": coin_id,
                    "symbol": symbol,
                    "available": bool(coin),
                    "price_usd": _safe_float(coin.get("usd")),
                    "change_24h_percent": _safe_float(
                        coin.get("usd_24h_change")
                    ),
                    "last_updated_at": coin.get("last_updated_at"),
                }
            )

        return {
            "available": any(asset["available"] for asset in assets),
            "assets": assets,
            "error": None,
        }

    except requests.RequestException as error:
        return {
            "available": False,
            "assets": [],
            "error": str(error),
        }
    except ValueError as error:
        return {
            "available": False,
            "assets": [],
            "error": f"Invalid JSON response: {error}",
        }


def get_market_status(force_refresh: bool = False) -> dict:
    global _market_cache, _market_cache_timestamp

    current_time = time.time()

    if (
        not force_refresh
        and _market_cache is not None
        and _market_cache_timestamp is not None
        and current_time - _market_cache_timestamp < CACHE_TTL_SECONDS
    ):
        cached_response = _market_cache.copy()
        cached_response["cached"] = True
        cached_response["cache_age_seconds"] = round(
            current_time - _market_cache_timestamp,
            1,
        )
        return cached_response

    alpha_vantage_api_key = os.getenv(
        "ALPHA_VANTAGE_API_KEY",
        "",
    ).strip()

    finnhub_api_key = os.getenv(
        "FINNHUB_API_KEY",
        "",
    ).strip()

    if alpha_vantage_api_key:
        market_session = _get_market_session_status(
            alpha_vantage_api_key
        )
    else:
        market_session = {
            "available": False,
            "us_equity": None,
            "error": "ALPHA_VANTAGE_API_KEY is not configured.",
        }

    stock_quotes = []

    if finnhub_api_key:
        for symbol in DEFAULT_STOCKS:
            stock_quotes.append(
                _get_stock_quote(symbol, finnhub_api_key)
            )

        stock_error = None
    else:
        stock_error = (
            "FINNHUB_API_KEY is not configured. "
            "Stock intelligence is currently unavailable."
        )

    crypto = _get_crypto_quotes()

    stocks_available = any(
        quote.get("available", False) for quote in stock_quotes
    )
    crypto_available = crypto.get("available", False)

    if stocks_available and crypto_available:
        status = "healthy"
        summary = "Stock and cryptocurrency market data retrieved."
    elif stocks_available or crypto_available:
        status = "warning"
        summary = "Market data was only partially retrieved."
    else:
        status = "unavailable"
        summary = "No live market data could be retrieved."

    errors = []

    if stock_error:
        errors.append(stock_error)

    if crypto.get("error"):
        errors.append(crypto["error"])

    result = {
        "status": status,
        "checked_at": _utc_now(),
        "summary": summary,
        "market_session": market_session,
        "stocks": {
            "provider": "Finnhub",
            "available": stocks_available,
            "watchlist": DEFAULT_STOCKS,
            "quotes": stock_quotes,
            "error": stock_error,
        },
        "crypto": {
            "provider": "CoinGecko",
            **crypto,
        },
        "errors": errors,
        "cached": False,
        "cache_age_seconds": 0.0,
    }

    _market_cache = result
    _market_cache_timestamp = current_time

    return result
