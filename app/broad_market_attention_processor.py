import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from app.broad_market_attention import (
    get_active_attention,
)
from app.market_db.recorder import (
    record_market_snapshot,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(PROJECT_ROOT / ".env")


FINNHUB_QUOTE_URL = (
    "https://finnhub.io/api/v1/quote"
)

REQUEST_TIMEOUT_SECONDS = 10

MAX_DEEP_ATTENTION_PER_CYCLE = 5
REQUEST_SPACING_SECONDS = 0.35
DEEP_ATTENTION_COOLDOWN_MINUTES = 30

STATE_DIRECTORY = (
    PROJECT_ROOT / "runtime"
)

PROCESSOR_STATE_FILE = (
    STATE_DIRECTORY
    / "broad_market_attention_processor_state.json"
)


DEEP_SELECTION_STATE_FILE = (
    STATE_DIRECTORY
    / "broad_market_deep_selection.json"
)


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


def _parse_datetime(
    value: str | None,
) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            value
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(
        timezone.utc
    )


def _load_processor_state() -> dict:
    try:
        with PROCESSOR_STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as handle:
            payload = json.load(handle)

        if isinstance(payload, dict):
            return payload

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        pass

    return {
        "last_processed_at": {},
    }


def _save_processor_state(
    state: dict,
) -> None:
    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    state["updated_at"] = utc_now()

    with PROCESSOR_STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            state,
            handle,
            indent=2,
        )


def _save_deep_selection(
    selected: list[dict],
) -> None:
    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = {
        "generated_at": utc_now(),
        "count": len(selected),
        "symbols": [
            attention["symbol"]
            for attention in selected
        ],
        "assets": [
            {
                "symbol": attention["symbol"],
                "interest_score": (
                    attention.get(
                        "interest_score"
                    )
                ),
                "persistence_matches": (
                    attention.get(
                        "persistence_matches",
                        0,
                    )
                ),
                "promotion_count": (
                    attention.get(
                        "promotion_count",
                        1,
                    )
                ),
                "priority_score": (
                    _priority_score(
                        attention
                    )
                ),
                "reasons": list(
                    attention.get(
                        "reasons",
                        [],
                    )
                ),
            }
            for attention in selected
        ],
    }

    with DEEP_SELECTION_STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            payload,
            handle,
            indent=2,
        )


def _cooldown_remaining_seconds(
    *,
    symbol: str,
    state: dict,
) -> float:
    last_processed = (
        state
        .get(
            "last_processed_at",
            {},
        )
        .get(symbol)
    )

    parsed = _parse_datetime(
        last_processed
    )

    if parsed is None:
        return 0.0

    cooldown_until = (
        parsed
        + timedelta(
            minutes=(
                DEEP_ATTENTION_COOLDOWN_MINUTES
            )
        )
    )

    remaining = (
        cooldown_until
        - datetime.now(
            timezone.utc
        )
    ).total_seconds()

    return max(
        0.0,
        remaining,
    )


def fetch_attention_quote(
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

        if response.status_code == 429:
            return {
                "symbol": symbol,
                "available": False,
                "rate_limited": True,
                "error": (
                    "Finnhub rate limit reached."
                ),
            }

        response.raise_for_status()

        payload = response.json()

        price = _safe_float(
            payload.get("c")
        )

        previous_close = _safe_float(
            payload.get("pc")
        )

        change_percent = _safe_float(
            payload.get("dp")
        )

        day_open = _safe_float(
            payload.get("o")
        )

        day_high = _safe_float(
            payload.get("h")
        )

        day_low = _safe_float(
            payload.get("l")
        )

        if (
            price is None
            or price <= 0
        ):
            return {
                "symbol": symbol,
                "available": False,
                "rate_limited": False,
                "error": (
                    "No valid quote returned."
                ),
            }

        intraday_range_percent = None

        if (
            day_high is not None
            and day_low is not None
            and day_low > 0
        ):
            intraday_range_percent = round(
                (
                    (
                        day_high
                        - day_low
                    )
                    / day_low
                )
                * 100,
                4,
            )

        return {
            "symbol": symbol,
            "available": True,
            "rate_limited": False,
            "price_usd": price,
            "previous_close_usd": (
                previous_close
            ),
            "change_percent": (
                change_percent
            ),
            "day_open_usd": day_open,
            "day_high_usd": day_high,
            "day_low_usd": day_low,
            "intraday_range_percent": (
                intraday_range_percent
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
            "rate_limited": False,
            "error": str(error),
        }


def evaluate_attention_state(
    *,
    attention: dict,
    quote: dict,
) -> dict:
    current_change = _safe_float(
        quote.get(
            "change_percent"
        )
    )

    source_score = _safe_float(
        attention.get(
            "interest_score"
        )
    ) or 0.0

    persistence_matches = int(
        attention.get(
            "persistence_matches",
            0,
        )
    )

    reasons = []

    if current_change is not None:
        if abs(current_change) >= 5.0:
            reasons.append(
                "Large move remains active."
            )

        elif abs(current_change) >= 2.0:
            reasons.append(
                "Elevated move remains active."
            )

    if persistence_matches >= 2:
        reasons.append(
            "Repeated directional persistence."
        )

    if source_score >= 50:
        reasons.append(
            "High promotion score."
        )

    continue_attention = bool(
        reasons
    )

    return {
        "continue_attention": (
            continue_attention
        ),
        "current_change_percent": (
            current_change
        ),
        "source_interest_score": (
            source_score
        ),
        "persistence_matches": (
            persistence_matches
        ),
        "reasons": reasons,
    }


def _priority_score(
    attention: dict,
) -> float:
    interest_score = _safe_float(
        attention.get(
            "interest_score"
        )
    ) or 0.0

    persistence_matches = int(
        attention.get(
            "persistence_matches",
            0,
        )
    )

    promotion_count = int(
        attention.get(
            "promotion_count",
            1,
        )
    )

    return round(
        interest_score
        + min(
            persistence_matches * 3.0,
            15.0,
        )
        + min(
            promotion_count * 2.0,
            10.0,
        ),
        2,
    )


def process_active_attention() -> dict:
    api_key = os.getenv(
        "FINNHUB_API_KEY",
        "",
    ).strip()

    if not api_key:
        raise RuntimeError(
            "FINNHUB_API_KEY is not configured."
        )

    active = list(
        get_active_attention()
    )

    processor_state = (
        _load_processor_state()
    )

    eligible = []
    cooling_down = []

    for attention in active:
        symbol = attention["symbol"]

        remaining = (
            _cooldown_remaining_seconds(
                symbol=symbol,
                state=processor_state,
            )
        )

        if remaining > 0:
            cooling_down.append(
                {
                    "symbol": symbol,
                    "cooldown_remaining_seconds": round(
                        remaining,
                        1,
                    ),
                }
            )
            continue

        eligible.append(
            attention
        )

    prioritized = sorted(
        eligible,
        key=_priority_score,
        reverse=True,
    )

    selected = prioritized[
        :MAX_DEEP_ATTENTION_PER_CYCLE
    ]

    deferred = prioritized[
        MAX_DEEP_ATTENTION_PER_CYCLE:
    ]

    _save_deep_selection(
        selected
    )

    results = []

    rate_limited = False

    for index, attention in enumerate(
        selected
    ):
        symbol = attention["symbol"]

        if index > 0:
            time.sleep(
                REQUEST_SPACING_SECONDS
            )

        quote = fetch_attention_quote(
            symbol=symbol,
            api_key=api_key,
        )

        if quote.get(
            "rate_limited",
            False,
        ):
            rate_limited = True

            results.append(
                {
                    "symbol": symbol,
                    "status": "rate_limited",
                    "priority_score": (
                        _priority_score(
                            attention
                        )
                    ),
                    "attention": attention,
                    "quote": quote,
                }
            )

            break

        if not quote.get(
            "available",
            False,
        ):
            results.append(
                {
                    "symbol": symbol,
                    "status": "unavailable",
                    "priority_score": (
                        _priority_score(
                            attention
                        )
                    ),
                    "attention": attention,
                    "quote": quote,
                }
            )
            continue

        storage = record_market_snapshot(
            {
                "stocks": {
                    "provider": (
                        "Finnhub Promoted Attention"
                    ),
                    "quotes": [
                        {
                            "symbol": symbol,
                            "available": True,
                            "price_usd": quote.get(
                                "price_usd"
                            ),
                            "change_percent": quote.get(
                                "change_percent"
                            ),
                        }
                    ],
                },
                "crypto": {
                    "provider": (
                        "Promoted Attention"
                    ),
                    "assets": [],
                },
            }
        )

        evaluation = (
            evaluate_attention_state(
                attention=attention,
                quote=quote,
            )
        )

        processor_state.setdefault(
            "last_processed_at",
            {},
        )[symbol] = utc_now()

        results.append(
            {
                "symbol": symbol,
                "status": "success",
                "priority_score": (
                    _priority_score(
                        attention
                    )
                ),
                "attention": attention,
                "quote": quote,
                "storage": storage,
                "evaluation": (
                    evaluation
                ),
            }
        )

    _save_processor_state(
        processor_state
    )

    continuing = [
        result
        for result in results
        if (
            result.get(
                "evaluation",
                {},
            ).get(
                "continue_attention",
                False,
            )
        )
    ]

    return {
        "status": (
            "rate_limited"
            if rate_limited
            else "success"
        ),
        "processed_at": utc_now(),
        "active_attention_count": len(
            active
        ),
        "eligible_count": len(
            eligible
        ),
        "cooldown_count": len(
            cooling_down
        ),
        "deep_attention_budget": (
            MAX_DEEP_ATTENTION_PER_CYCLE
        ),
        "selected_count": len(
            selected
        ),
        "processed_count": len(
            results
        ),
        "deferred_count": len(
            deferred
        ),
        "continuing_attention_count": len(
            continuing
        ),
        "rate_limited": rate_limited,
        "selected_symbols": [
            attention["symbol"]
            for attention in selected
        ],
        "deferred_symbols": [
            attention["symbol"]
            for attention in deferred
        ],
        "cooling_down": cooling_down,
        "results": results,
    }


if __name__ == "__main__":
    print(
        json.dumps(
            process_active_attention(),
            indent=2,
        )
    )
