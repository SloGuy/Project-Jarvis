from dataclasses import dataclass
from pathlib import Path
import csv
import io
import os

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


FINNHUB_STOCK_SYMBOL_URL = (
    "https://finnhub.io/api/v1/stock/symbol"
)

SP500_CONSTITUENTS_URL = (
    "https://raw.githubusercontent.com/"
    "datasets/s-and-p-500-companies/"
    "main/data/constituents.csv"
)

REQUEST_TIMEOUT_SECONDS = 30


MAJOR_US_EXCHANGE_MICS = {
    "XNAS",
    "XNYS",
    "XASE",
    "BATS",
}


ALLOWED_SECURITY_TYPES = {
    "Common Stock",
    "ADR",
    "REIT",
}


@dataclass(frozen=True)
class BroadStockCandidate:
    symbol: str
    name: str
    mic: str = ""
    security_type: str = ""


BOOTSTRAP_STOCK_UNIVERSE = (
    BroadStockCandidate(
        symbol="MSFT",
        name="Microsoft",
    ),
    BroadStockCandidate(
        symbol="AMZN",
        name="Amazon",
    ),
    BroadStockCandidate(
        symbol="META",
        name="Meta Platforms",
    ),
    BroadStockCandidate(
        symbol="GOOGL",
        name="Alphabet",
    ),
    BroadStockCandidate(
        symbol="NFLX",
        name="Netflix",
    ),
    BroadStockCandidate(
        symbol="AMD",
        name="Advanced Micro Devices",
    ),
    BroadStockCandidate(
        symbol="AVGO",
        name="Broadcom",
    ),
    BroadStockCandidate(
        symbol="JPM",
        name="JPMorgan Chase",
    ),
    BroadStockCandidate(
        symbol="XOM",
        name="Exxon Mobil",
    ),
    BroadStockCandidate(
        symbol="WMT",
        name="Walmart",
    ),
)


def _get_finnhub_api_key() -> str:
    return os.getenv(
        "FINNHUB_API_KEY",
        "",
    ).strip()


def _normalize_symbol(symbol: str) -> str:
    return (
        symbol
        .strip()
        .upper()
        .replace("-", ".")
    )


def fetch_finnhub_stock_candidates() -> tuple[
    BroadStockCandidate,
    ...,
]:
    api_key = _get_finnhub_api_key()

    if not api_key:
        raise RuntimeError(
            "FINNHUB_API_KEY is not configured."
        )

    response = requests.get(
        FINNHUB_STOCK_SYMBOL_URL,
        params={
            "exchange": "US",
            "token": api_key,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, list):
        raise RuntimeError(
            "Unexpected Finnhub stock-symbol response."
        )

    candidates = []
    seen_symbols = set()

    for row in payload:
        if not isinstance(row, dict):
            continue

        symbol = str(
            row.get("symbol", "")
        ).strip().upper()

        name = str(
            row.get("description", "")
        ).strip()

        mic = str(
            row.get("mic", "")
        ).strip().upper()

        security_type = str(
            row.get("type", "")
        ).strip()

        currency = str(
            row.get("currency", "")
        ).strip().upper()

        if not symbol:
            continue

        if symbol in seen_symbols:
            continue

        if currency != "USD":
            continue

        if mic not in MAJOR_US_EXCHANGE_MICS:
            continue

        if security_type not in ALLOWED_SECURITY_TYPES:
            continue

        seen_symbols.add(symbol)

        candidates.append(
            BroadStockCandidate(
                symbol=symbol,
                name=name or symbol,
                mic=mic,
                security_type=security_type,
            )
        )

    candidates.sort(
        key=lambda asset: asset.symbol
    )

    return tuple(candidates)


def fetch_sp500_symbols() -> tuple[str, ...]:
    response = requests.get(
        SP500_CONSTITUENTS_URL,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    reader = csv.DictReader(
        io.StringIO(response.text)
    )

    symbols = []

    for row in reader:
        symbol = str(
            row.get("Symbol", "")
        ).strip()

        if not symbol:
            continue

        symbols.append(
            _normalize_symbol(symbol)
        )

    if not symbols:
        raise RuntimeError(
            "No S&P 500 constituents were returned."
        )

    return tuple(symbols)


def fetch_sp500_stock_candidates() -> tuple[
    BroadStockCandidate,
    ...,
]:
    finnhub_candidates = (
        fetch_finnhub_stock_candidates()
    )

    sp500_symbols = set(
        fetch_sp500_symbols()
    )

    candidates = []

    for asset in finnhub_candidates:
        normalized_symbol = (
            _normalize_symbol(
                asset.symbol
            )
        )

        if normalized_symbol not in sp500_symbols:
            continue

        candidates.append(asset)

    candidates.sort(
        key=lambda asset: asset.symbol
    )

    return tuple(candidates)


def get_broad_stock_candidates(
    *,
    use_provider: bool = False,
    limit: int | None = None,
) -> tuple[BroadStockCandidate, ...]:
    if not use_provider:
        candidates = BOOTSTRAP_STOCK_UNIVERSE
    else:
        try:
            candidates = (
                fetch_sp500_stock_candidates()
            )
        except Exception:
            candidates = BOOTSTRAP_STOCK_UNIVERSE

        if not candidates:
            candidates = BOOTSTRAP_STOCK_UNIVERSE

    if limit is not None:
        return candidates[:limit]

    return candidates
