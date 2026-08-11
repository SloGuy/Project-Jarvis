import threading
from datetime import datetime, timezone
from typing import Any


_state_lock = threading.Lock()

_latest_quotes: dict[str, dict[str, Any]] = {}

_provider_status: dict[str, dict[str, Any]] = {
    "finnhub": {
        "connected": False,
        "connected_at": None,
        "last_message_at": None,
        "last_error": None,
    },
    "coingecko": {
        "connected": False,
        "connected_at": None,
        "last_message_at": None,
        "last_error": None,
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def update_quote(
    symbol: str,
    quote: dict[str, Any],
) -> None:
    normalized_symbol = symbol.strip().upper()

    if not normalized_symbol:
        return

    with _state_lock:
        _latest_quotes[normalized_symbol] = {
            **quote,
            "symbol": normalized_symbol,
        }


def update_provider_status(
    provider: str,
    **values: Any,
) -> None:
    normalized_provider = provider.strip().lower()

    with _state_lock:
        status = _provider_status.setdefault(
            normalized_provider,
            {},
        )

        status.update(values)


def get_live_quote(
    symbol: str,
) -> dict[str, Any] | None:
    normalized_symbol = symbol.strip().upper()

    with _state_lock:
        quote = _latest_quotes.get(
            normalized_symbol
        )

        if quote is None:
            return None

        return quote.copy()


def get_live_quotes() -> dict[str, dict[str, Any]]:
    with _state_lock:
        return {
            symbol: quote.copy()
            for symbol, quote in _latest_quotes.items()
        }


def get_provider_status() -> dict[str, dict[str, Any]]:
    with _state_lock:
        return {
            provider: status.copy()
            for provider, status
            in _provider_status.items()
        }


def get_live_market_status() -> dict[str, Any]:
    quotes = get_live_quotes()

    return {
        "status": "success",
        "generated_at": utc_now(),
        "quote_count": len(quotes),
        "providers": get_provider_status(),
        "quotes": quotes,
    }
