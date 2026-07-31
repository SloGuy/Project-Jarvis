from datetime import datetime, timezone
import os
import time
from typing import Any

import requests


ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"

DEFAULT_STOCKS = ["SPY", "QQQ", "DIA"]
DEFAULT_CRYPTO = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
}

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
            ALPHA_VANTAGE_URL,
            params={
                "function": "GLOBAL_QUOTE",
                "symbol": symbol,
                "apikey": api_key,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()

        quote = payload.get("Global Quote", {})

        if not quote:
            message = (
                payload.get("Information")
                or payload.get("Note")
                or payload.get("Error Message")
                or "No quote data returned."
            )

            return {
                "symbol": symbol,
                "available": False,
                "error": message,
            }

        price = _safe_float(quote.get("05. price"))
        previous_close = _safe_float(quote.get("08. previous close"))
        change = _safe_float(quote.get("09. change"))

        change_percent_raw = quote.get("10. change percent", "")
        change_percent = _safe_float(
            str(change_percent_raw).replace("%", "")
        )

        return {
            "symbol": symbol,
            "available": True,
            "price_usd": price,
            "previous_close_usd": previous_close,
            "change_usd": change,
            "change_percent": change_percent,
            "latest_trading_day": quote.get("07. latest trading day"),
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


def get_market_status() -> dict:
    global _market_cache, _market_cache_timestamp

    current_time = time.time()

    if (
        _market_cache is not None
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

    if alpha_vantage_api_key:
        stock_quotes = []

        for index, symbol in enumerate(DEFAULT_STOCKS):
            stock_quotes.append(
                _get_stock_quote(symbol, alpha_vantage_api_key)
            )

            if index < len(DEFAULT_STOCKS) - 1:
                time.sleep(1.1)

        stock_error = None
    else:
        stock_quotes = []
        stock_error = (
            "ALPHA_VANTAGE_API_KEY is not configured. "
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
        "stocks": {
            "provider": "Alpha Vantage",
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
