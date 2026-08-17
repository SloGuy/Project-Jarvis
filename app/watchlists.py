import json
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

STATE_DIRECTORY = (
    PROJECT_ROOT / "runtime"
)

WATCHLIST_STATE_FILE = (
    STATE_DIRECTORY
    / "watchlists.json"
)


DEFAULT_WATCHLISTS = {
    "stocks": [
        "AAPL",
        "TSLA",
        "NVDA",
        "MSFT",
        "AMZN",
        "META",
        "GOOGL",
        "AMD",
    ],
    "crypto": [
        "BTC",
        "ETH",
        "SOL",
        "XMR",
        "XRP",
    ],
}


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _normalize_symbol(
    symbol: str,
) -> str:
    return (
        symbol
        .strip()
        .upper()
    )


def _default_state() -> dict:
    return {
        "stocks": list(
            DEFAULT_WATCHLISTS["stocks"]
        ),
        "crypto": list(
            DEFAULT_WATCHLISTS["crypto"]
        ),
        "updated_at": utc_now(),
    }


def _load_state() -> dict:
    try:
        with WATCHLIST_STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as handle:
            payload = json.load(handle)

        if not isinstance(
            payload,
            dict,
        ):
            raise ValueError(
                "Watchlist state must be a dictionary."
            )

        stocks = payload.get(
            "stocks",
            [],
        )

        crypto = payload.get(
            "crypto",
            [],
        )

        if not isinstance(
            stocks,
            list,
        ):
            stocks = []

        if not isinstance(
            crypto,
            list,
        ):
            crypto = []

        return {
            "stocks": [
                _normalize_symbol(
                    symbol
                )
                for symbol in stocks
                if str(
                    symbol
                ).strip()
            ],
            "crypto": [
                _normalize_symbol(
                    symbol
                )
                for symbol in crypto
                if str(
                    symbol
                ).strip()
            ],
            "updated_at": payload.get(
                "updated_at"
            ),
        }

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
        ValueError,
    ):
        state = _default_state()

        _save_state(
            state
        )

        return state


def _save_state(
    state: dict,
) -> None:
    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    state["updated_at"] = (
        utc_now()
    )

    with WATCHLIST_STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            state,
            handle,
            indent=2,
        )


def get_watchlists() -> dict:
    state = _load_state()

    return {
        "status": "success",
        "stocks": list(
            state.get(
                "stocks",
                [],
            )
        ),
        "crypto": list(
            state.get(
                "crypto",
                [],
            )
        ),
        "stock_count": len(
            state.get(
                "stocks",
                [],
            )
        ),
        "crypto_count": len(
            state.get(
                "crypto",
                [],
            )
        ),
        "total_assets": (
            len(
                state.get(
                    "stocks",
                    [],
                )
            )
            + len(
                state.get(
                    "crypto",
                    [],
                )
            )
        ),
        "updated_at": state.get(
            "updated_at"
        ),
    }


def add_to_watchlist(
    *,
    asset_type: str,
    symbol: str,
) -> dict:
    asset_type = (
        asset_type
        .strip()
        .lower()
    )

    symbol = _normalize_symbol(
        symbol
    )

    if asset_type not in {
        "stocks",
        "crypto",
    }:
        raise ValueError(
            "asset_type must be stocks or crypto"
        )

    if not symbol:
        raise ValueError(
            "symbol is required"
        )

    state = _load_state()

    watchlist = state.setdefault(
        asset_type,
        [],
    )

    if symbol not in watchlist:
        watchlist.append(
            symbol
        )

    _save_state(
        state
    )

    return get_watchlists()


def remove_from_watchlist(
    *,
    asset_type: str,
    symbol: str,
) -> dict:
    asset_type = (
        asset_type
        .strip()
        .lower()
    )

    symbol = _normalize_symbol(
        symbol
    )

    if asset_type not in {
        "stocks",
        "crypto",
    }:
        raise ValueError(
            "asset_type must be stocks or crypto"
        )

    state = _load_state()

    watchlist = state.setdefault(
        asset_type,
        [],
    )

    state[asset_type] = [
        existing
        for existing in watchlist
        if existing != symbol
    ]

    _save_state(
        state
    )

    return get_watchlists()


if __name__ == "__main__":
    print(
        json.dumps(
            get_watchlists(),
            indent=2,
        )
    )
