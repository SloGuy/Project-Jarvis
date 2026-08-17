import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from app.live_market.live_state import get_live_quotes
from app.watchlists import get_watchlists


PROJECT_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(PROJECT_ROOT / ".env")


FINNHUB_QUOTE_URL = (
    "https://finnhub.io/api/v1/quote"
)

COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
)

REQUEST_TIMEOUT_SECONDS = 10


CRYPTO_PROVIDER_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XMR": "monero",
    "XRP": "ripple",
}


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _safe_float(
    value,
) -> float | None:
    try:
        return float(value)
    except (
        TypeError,
        ValueError,
    ):
        return None


def _fetch_stock_quote(
    symbol: str,
    api_key: str,
) -> dict:
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

    price = _safe_float(
        payload.get("c")
    )

    if (
        price is None
        or price <= 0
    ):
        raise RuntimeError(
            f"No valid quote returned for {symbol}."
        )

    return {
        "symbol": symbol,
        "price_usd": price,
        "change_percent": _safe_float(
            payload.get("dp")
        ),
        "source": "finnhub_snapshot",
        "quote_timestamp": payload.get(
            "t"
        ),
    }


def _fetch_crypto_quotes(
    symbols: list[str],
) -> dict[str, dict]:
    provider_ids = {
        symbol: CRYPTO_PROVIDER_IDS.get(
            symbol
        )
        for symbol in symbols
    }

    provider_ids = {
        symbol: provider_id
        for symbol, provider_id
        in provider_ids.items()
        if provider_id
    }

    if not provider_ids:
        return {}

    response = requests.get(
        COINGECKO_URL,
        params={
            "ids": ",".join(
                provider_ids.values()
            ),
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_last_updated_at": "true",
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    payload = response.json()

    quotes = {}

    for symbol, provider_id in (
        provider_ids.items()
    ):
        coin = payload.get(
            provider_id,
            {},
        )

        price = _safe_float(
            coin.get("usd")
        )

        if (
            price is None
            or price <= 0
        ):
            continue

        quotes[symbol] = {
            "symbol": symbol,
            "price_usd": price,
            "change_percent": _safe_float(
                coin.get(
                    "usd_24h_change"
                )
            ),
            "source": "coingecko_snapshot",
            "quote_timestamp": (
                coin.get(
                    "last_updated_at"
                )
            ),
        }

    return quotes


def get_watchlist_quotes() -> dict:
    watchlists = get_watchlists()

    stocks = watchlists.get(
        "stocks",
        [],
    )

    crypto = watchlists.get(
        "crypto",
        [],
    )

    live_quotes = get_live_quotes()

    finnhub_api_key = os.getenv(
        "FINNHUB_API_KEY",
        "",
    ).strip()

    stock_quotes = []

    for symbol in stocks:
        live = live_quotes.get(
            symbol
        )

        if live:
            stock_quotes.append(
                {
                    "symbol": symbol,
                    "price_usd": (
                        live.get(
                            "price_usd"
                        )
                    ),
                    "change_percent": (
                        live.get(
                            "change_percent"
                        )
                    ),
                    "source": "live",
                    "observed_at": (
                        live.get(
                            "observed_at"
                        )
                    ),
                }
            )
            continue

        if not finnhub_api_key:
            stock_quotes.append(
                {
                    "symbol": symbol,
                    "price_usd": None,
                    "change_percent": None,
                    "source": "unavailable",
                }
            )
            continue

        try:
            stock_quotes.append(
                _fetch_stock_quote(
                    symbol,
                    finnhub_api_key,
                )
            )
        except Exception:
            stock_quotes.append(
                {
                    "symbol": symbol,
                    "price_usd": None,
                    "change_percent": None,
                    "source": "unavailable",
                }
            )

    crypto_quotes_map = (
        _fetch_crypto_quotes(
            crypto
        )
    )

    crypto_quotes = [
        crypto_quotes_map.get(
            symbol,
            {
                "symbol": symbol,
                "price_usd": None,
                "change_percent": None,
                "source": "unavailable",
            },
        )
        for symbol in crypto
    ]

    return {
        "status": "success",
        "generated_at": utc_now(),
        "stocks": stock_quotes,
        "crypto": crypto_quotes,
        "stock_count": len(
            stock_quotes
        ),
        "crypto_count": len(
            crypto_quotes
        ),
    }
