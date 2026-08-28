import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from app.market_universe import get_market_assets
from app.broad_market_attention import promote_asset


PROJECT_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(PROJECT_ROOT / ".env")


FINNHUB_QUOTE_URL = (
    "https://finnhub.io/api/v1/quote"
)

REQUEST_TIMEOUT_SECONDS = 10

DEFAULT_BATCH_SIZE = 25
DEFAULT_TOP_N = 10
MIN_INTEREST_SCORE = 10.0

MAX_OBSERVATIONS_PER_SYMBOL = 5
PERSISTENCE_BONUS_PER_MATCH = 5.0

PROMOTION_MIN_SCORE = 35.0
PROMOTION_MIN_PERSISTENCE_MATCHES = 1
PROMOTION_STRONG_MOVE_PERCENT = 5.0


STATE_DIRECTORY = (
    PROJECT_ROOT / "runtime"
)

CURSOR_STATE_FILE = (
    STATE_DIRECTORY
    / "broad_market_scanner_state.json"
)

OBSERVATION_STATE_FILE = (
    STATE_DIRECTORY
    / "broad_market_observations.json"
)

LATEST_SCAN_STATE_FILE = (
    STATE_DIRECTORY
    / "broad_market_latest_scan.json"
)


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def get_broad_stock_symbols() -> tuple[str, ...]:
    return tuple(
        asset.symbol
        for asset in get_market_assets(
            asset_type="stock",
            tracking_tier="broad",
        )
        if asset.snapshot_enabled
    )


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


def _load_cursor() -> int:
    try:
        with CURSOR_STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as handle:
            payload = json.load(handle)

        return int(
            payload.get("cursor", 0)
        )

    except (
        FileNotFoundError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        return 0


def _save_cursor(
    cursor: int,
) -> None:
    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = {
        "cursor": cursor,
        "updated_at": utc_now(),
    }

    with CURSOR_STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            payload,
            handle,
            indent=2,
        )


def _load_observations() -> dict:
    try:
        with OBSERVATION_STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as handle:
            payload = json.load(handle)

        if isinstance(payload, dict):
            return payload

    except (
        FileNotFoundError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        pass

    return {}


def _save_observations(
    observations: dict,
) -> None:
    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OBSERVATION_STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            observations,
            handle,
            indent=2,
        )


def _save_latest_scan(
    payload: dict,
) -> None:
    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    with LATEST_SCAN_STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            payload,
            handle,
            indent=2,
        )


def get_broad_market_scanner_snapshot() -> dict:
    try:
        with LATEST_SCAN_STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as handle:
            payload = json.load(handle)

        if isinstance(payload, dict):
            return payload

    except (
        FileNotFoundError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        pass

    return {
        "status": "unavailable",
        "scanned_at": None,
        "total_broad_universe": len(
            get_broad_stock_symbols()
        ),
        "batch_size": 0,
        "batch_cursor": _load_cursor(),
        "next_cursor": _load_cursor(),
        "symbols_scanned": [],
        "available_count": 0,
        "failure_count": 0,
        "interesting_count": 0,
        "promotion_candidate_count": 0,
        "interesting_assets": [],
        "promotion_candidates": [],
        "failures": [],
    }


def get_scan_batch(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[
    tuple[str, ...],
    int,
    int,
]:
    symbols = get_broad_stock_symbols()

    universe_size = len(symbols)

    if universe_size == 0:
        return (), 0, 0

    batch_size = max(
        1,
        min(
            batch_size,
            universe_size,
        ),
    )

    cursor = (
        _load_cursor()
        % universe_size
    )

    batch = tuple(
        symbols[
            (cursor + offset)
            % universe_size
        ]
        for offset in range(
            batch_size
        )
    )

    next_cursor = (
        cursor + batch_size
    ) % universe_size

    return (
        batch,
        cursor,
        next_cursor,
    )


def fetch_stock_quote(
    symbol: str,
    api_key: str,
) -> dict:
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

        price = _safe_float(
            payload.get("c")
        )

        change_percent = _safe_float(
            payload.get("dp")
        )

        previous_close = _safe_float(
            payload.get("pc")
        )

        if (
            price is None
            or price <= 0
        ):
            return {
                "symbol": symbol,
                "available": False,
                "error": (
                    "No valid quote returned."
                ),
            }

        return {
            "symbol": symbol,
            "available": True,
            "price_usd": price,
            "previous_close_usd": (
                previous_close
            ),
            "change_percent": (
                change_percent
            ),
            "quote_timestamp": (
                payload.get("t")
            ),
            "error": None,
        }

    except Exception as error:
        return {
            "symbol": symbol,
            "available": False,
            "error": str(error),
        }


def _direction(
    change_percent: float | None,
) -> str:
    if change_percent is None:
        return "flat"

    if change_percent > 0:
        return "up"

    if change_percent < 0:
        return "down"

    return "flat"


def calculate_base_interest_score(
    quote: dict,
) -> float:
    change_percent = (
        quote.get("change_percent")
    )

    if change_percent is None:
        return 0.0

    return round(
        min(
            abs(change_percent) * 10,
            100.0,
        ),
        2,
    )


def calculate_persistence_bonus(
    *,
    symbol: str,
    change_percent: float | None,
    observations: dict,
) -> tuple[
    float,
    int,
]:
    if change_percent is None:
        return 0.0, 0

    current_direction = _direction(
        change_percent
    )

    if current_direction == "flat":
        return 0.0, 0

    history = observations.get(
        symbol,
        [],
    )

    matching = 0

    for observation in reversed(history):
        prior_change = _safe_float(
            observation.get(
                "change_percent"
            )
        )

        if (
            _direction(prior_change)
            != current_direction
        ):
            break

        matching += 1

    bonus = min(
        matching
        * PERSISTENCE_BONUS_PER_MATCH,
        20.0,
    )

    return (
        round(bonus, 2),
        matching,
    )


def record_observation(
    *,
    symbol: str,
    quote: dict,
    observations: dict,
) -> None:
    history = observations.setdefault(
        symbol,
        [],
    )

    history.append(
        {
            "observed_at": utc_now(),
            "price_usd": quote.get(
                "price_usd"
            ),
            "change_percent": quote.get(
                "change_percent"
            ),
            "interest_score": quote.get(
                "interest_score"
            ),
            "quote_timestamp": quote.get(
                "quote_timestamp"
            ),
        }
    )

    observations[symbol] = (
        history[
            -MAX_OBSERVATIONS_PER_SYMBOL:
        ]
    )


def classify_interest(
    score: float,
) -> str:
    if score >= 50:
        return "high"

    if score >= 25:
        return "elevated"

    if score >= MIN_INTEREST_SCORE:
        return "interesting"

    return "normal"


def evaluate_promotion(
    quote: dict,
) -> dict:
    score = _safe_float(
        quote.get("interest_score")
    ) or 0.0

    change_percent = _safe_float(
        quote.get("change_percent")
    ) or 0.0

    persistence_matches = int(
        quote.get(
            "persistence_matches",
            0,
        )
    )

    reasons = []

    if (
        score >= PROMOTION_MIN_SCORE
        and persistence_matches
        >= PROMOTION_MIN_PERSISTENCE_MATCHES
    ):
        reasons.append(
            "Sustained elevated interest across scans."
        )

    if (
        abs(change_percent)
        >= PROMOTION_STRONG_MOVE_PERCENT
        and persistence_matches
        >= PROMOTION_MIN_PERSISTENCE_MATCHES
    ):
        reasons.append(
            "Large move persisted across scans."
        )

    eligible = bool(reasons)

    return {
        "eligible": eligible,
        "score": score,
        "persistence_matches": (
            persistence_matches
        ),
        "reasons": reasons,
    }


def scan_broad_market(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    top_n: int = DEFAULT_TOP_N,
) -> dict:
    api_key = os.getenv(
        "FINNHUB_API_KEY",
        "",
    ).strip()

    if not api_key:
        raise RuntimeError(
            "FINNHUB_API_KEY is not configured."
        )

    observations = (
        _load_observations()
    )

    (
        symbols,
        cursor,
        next_cursor,
    ) = get_scan_batch(
        batch_size=batch_size,
    )

    results = []
    failures = []
    promotion_candidates = []

    for symbol in symbols:
        quote = fetch_stock_quote(
            symbol,
            api_key,
        )

        if not quote.get(
            "available",
            False,
        ):
            failures.append(quote)
            continue

        history = observations.get(
            symbol,
            [],
        )

        last_quote_timestamp = (
            history[-1].get("quote_timestamp")
            if history
            else None
        )

        quote_is_fresh = (
            quote.get("quote_timestamp")
            != last_quote_timestamp
        )

        quote["quote_is_fresh"] = (
            quote_is_fresh
        )

        base_score = (
            calculate_base_interest_score(
                quote
            )
        )

        (
            persistence_bonus,
            persistence_matches,
        ) = calculate_persistence_bonus(
            symbol=symbol,
            change_percent=quote.get(
                "change_percent"
            ),
            observations=observations,
        )

        final_score = round(
            min(
                base_score
                + persistence_bonus,
                100.0,
            ),
            2,
        )

        quote["base_interest_score"] = (
            base_score
        )

        quote["persistence_bonus"] = (
            persistence_bonus
        )

        quote["persistence_matches"] = (
            persistence_matches
        )

        quote["interest_score"] = (
            final_score
        )

        quote["interest_level"] = (
            classify_interest(
                final_score
            )
        )

        promotion = (
            evaluate_promotion(
                quote
            )
        )

        quote["promotion"] = promotion

        if (
            promotion["eligible"]
            and quote_is_fresh
        ):
            attention_result = promote_asset(
                symbol=symbol,
                interest_score=final_score,
                persistence_matches=(
                    persistence_matches
                ),
                reasons=promotion[
                    "reasons"
                ],
            )

            promotion_candidates.append(
                {
                    "symbol": symbol,
                    "price_usd": (
                        quote.get(
                            "price_usd"
                        )
                    ),
                    "change_percent": (
                        quote.get(
                            "change_percent"
                        )
                    ),
                    "interest_score": (
                        final_score
                    ),
                    "persistence_matches": (
                        persistence_matches
                    ),
                    "reasons": (
                        promotion["reasons"]
                    ),
                    "attention": (
                        attention_result[
                            "promotion"
                        ]
                    ),
                }
            )

        if quote_is_fresh:
            record_observation(
                symbol=symbol,
                quote=quote,
                observations=observations,
            )

        results.append(quote)

    ranked = sorted(
        results,
        key=lambda item: (
            item.get(
                "interest_score",
                0.0,
            )
        ),
        reverse=True,
    )

    interesting = [
        asset
        for asset in ranked
        if asset["interest_score"]
        >= MIN_INTEREST_SCORE
    ][:top_n]

    promotion_candidates.sort(
        key=lambda item: (
            item.get(
                "interest_score",
                0.0,
            )
        ),
        reverse=True,
    )

    _save_observations(
        observations
    )

    _save_cursor(
        next_cursor
    )

    scan_result = {
        "status": "success",
        "scanned_at": utc_now(),
        "total_broad_universe": len(
            get_broad_stock_symbols()
        ),
        "batch_size": len(symbols),
        "batch_cursor": cursor,
        "next_cursor": next_cursor,
        "symbols_scanned": list(symbols),
        "available_count": len(results),
        "failure_count": len(failures),
        "interesting_count": len(
            interesting
        ),
        "promotion_candidate_count": len(
            promotion_candidates
        ),
        "interesting_assets": (
            interesting
        ),
        "promotion_candidates": (
            promotion_candidates
        ),
        "failures": failures,
    }

    _save_latest_scan(
        scan_result
    )

    return scan_result


if __name__ == "__main__":
    print(
        json.dumps(
            scan_broad_market(),
            indent=2,
        )
    )
