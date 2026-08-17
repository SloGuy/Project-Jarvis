import json
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from pathlib import Path

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import MarketAsset
from app.market_universe import (
    get_market_assets,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

STATE_DIRECTORY = (
    PROJECT_ROOT / "runtime"
)

ATTENTION_STATE_FILE = (
    STATE_DIRECTORY
    / "broad_market_attention.json"
)

DEEP_SELECTION_STATE_FILE = (
    STATE_DIRECTORY
    / "broad_market_deep_selection.json"
)

PROCESSOR_STATE_FILE = (
    STATE_DIRECTORY
    / "broad_market_attention_processor_state.json"
)

DEFAULT_ATTENTION_HOURS = 12


def utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


def utc_now_iso() -> str:
    return utc_now().isoformat()


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


def _load_state() -> dict:
    try:
        with ATTENTION_STATE_FILE.open(
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
        "active": {},
    }


def _save_state(
    state: dict,
) -> None:
    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    with ATTENTION_STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            state,
            handle,
            indent=2,
        )


def _get_base_broad_symbols() -> set[str]:
    return {
        asset.symbol
        for asset in get_market_assets(
            tracking_tier="broad",
        )
    }


def _deactivate_expired_market_assets(
    symbols: list[str],
) -> list[str]:
    if not symbols:
        return []

    broad_symbols = (
        _get_base_broad_symbols()
    )

    eligible_symbols = {
        symbol.strip().upper()
        for symbol in symbols
        if (
            symbol
            and symbol.strip().upper()
            in broad_symbols
        )
    }

    if not eligible_symbols:
        return []

    deactivated = []

    with SessionLocal() as session:
        assets = session.scalars(
            select(MarketAsset).where(
                MarketAsset.symbol.in_(
                    eligible_symbols
                ),
                MarketAsset.is_active.is_(
                    True
                ),
            )
        ).all()

        for asset in assets:
            asset.is_active = False

            deactivated.append(
                asset.symbol
            )

        session.commit()

    return sorted(
        deactivated
    )


def _remove_from_deep_selection(
    symbols: list[str],
) -> list[str]:
    if not symbols:
        return []

    expired_symbols = {
        symbol.strip().upper()
        for symbol in symbols
        if symbol
    }

    try:
        with DEEP_SELECTION_STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as handle:
            payload = json.load(
                handle
            )

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        return []

    existing_symbols = payload.get(
        "symbols",
        [],
    )

    assets = payload.get(
        "assets",
        [],
    )

    removed = [
        symbol
        for symbol in existing_symbols
        if symbol in expired_symbols
    ]

    if not removed:
        return []

    payload["symbols"] = [
        symbol
        for symbol in existing_symbols
        if symbol not in expired_symbols
    ]

    payload["assets"] = [
        asset
        for asset in assets
        if (
            asset.get("symbol")
            not in expired_symbols
        )
    ]

    payload["count"] = len(
        payload["symbols"]
    )

    payload["updated_at"] = (
        utc_now_iso()
    )

    with DEEP_SELECTION_STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            payload,
            handle,
            indent=2,
        )

    return sorted(
        removed
    )


def _remove_from_processor_state(
    symbols: list[str],
) -> list[str]:
    if not symbols:
        return []

    expired_symbols = {
        symbol.strip().upper()
        for symbol in symbols
        if symbol
    }

    try:
        with PROCESSOR_STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as handle:
            payload = json.load(
                handle
            )

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        return []

    last_processed = payload.get(
        "last_processed_at",
        {},
    )

    removed = []

    for symbol in expired_symbols:
        if symbol in last_processed:
            last_processed.pop(
                symbol,
                None,
            )

            removed.append(
                symbol
            )

    if not removed:
        return []

    payload["last_processed_at"] = (
        last_processed
    )

    payload["updated_at"] = (
        utc_now_iso()
    )

    with PROCESSOR_STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            payload,
            handle,
            indent=2,
        )

    return sorted(
        removed
    )


def _apply_demotion(
    expired: list[dict],
) -> dict:
    symbols = [
        record.get("symbol", "")
        for record in expired
        if record.get("symbol")
    ]

    deactivated_assets = (
        _deactivate_expired_market_assets(
            symbols
        )
    )

    removed_from_selection = (
        _remove_from_deep_selection(
            symbols
        )
    )

    removed_from_processor_state = (
        _remove_from_processor_state(
            symbols
        )
    )

    return {
        "expired_symbols": sorted(
            symbols
        ),
        "deactivated_market_assets": (
            deactivated_assets
        ),
        "removed_from_deep_selection": (
            removed_from_selection
        ),
        "removed_from_processor_state": (
            removed_from_processor_state
        ),
    }


def expire_attention(
    state: dict | None = None,
) -> tuple[
    dict,
    list[dict],
]:
    if state is None:
        state = _load_state()

    active = state.setdefault(
        "active",
        {},
    )

    now = utc_now()

    expired = []

    for symbol in list(active):
        record = active[symbol]

        expires_at = _parse_datetime(
            record.get("expires_at")
        )

        if (
            expires_at is not None
            and expires_at <= now
        ):
            expired.append(
                active.pop(symbol)
            )

    if expired:
        demotion = _apply_demotion(
            expired
        )

        state["last_demotion"] = {
            "demoted_at": (
                now.isoformat()
            ),
            **demotion,
        }

    return state, expired


def promote_asset(
    *,
    symbol: str,
    interest_score: float,
    persistence_matches: int,
    reasons: list[str],
    attention_hours: int = (
        DEFAULT_ATTENTION_HOURS
    ),
) -> dict:
    symbol = symbol.strip().upper()

    if not symbol:
        raise ValueError(
            "symbol is required"
        )

    state = _load_state()

    state, expired = (
        expire_attention(state)
    )

    active = state.setdefault(
        "active",
        {},
    )

    now = utc_now()

    expires_at = (
        now
        + timedelta(
            hours=attention_hours
        )
    )

    existing = active.get(
        symbol
    )

    if existing:
        promoted_at = existing.get(
            "promoted_at"
        )

        promotion_count = int(
            existing.get(
                "promotion_count",
                1,
            )
        ) + 1
    else:
        promoted_at = (
            now.isoformat()
        )

        promotion_count = 1

    record = {
        "symbol": symbol,
        "status": "active",
        "promoted_at": promoted_at,
        "last_promoted_at": (
            now.isoformat()
        ),
        "expires_at": (
            expires_at.isoformat()
        ),
        "attention_hours": (
            attention_hours
        ),
        "interest_score": round(
            float(
                interest_score
            ),
            2,
        ),
        "persistence_matches": int(
            persistence_matches
        ),
        "reasons": list(
            reasons
        ),
        "promotion_count": (
            promotion_count
        ),
    }

    active[symbol] = record

    state["updated_at"] = (
        now.isoformat()
    )

    _save_state(
        state
    )

    return {
        "status": "success",
        "promotion": record,
        "expired_count": len(
            expired
        ),
        "expired": expired,
        "demotion": state.get(
            "last_demotion"
        ),
    }


def get_active_attention() -> tuple[
    dict,
    ...,
]:
    state = _load_state()

    state, expired = (
        expire_attention(state)
    )

    if expired:
        state["updated_at"] = (
            utc_now_iso()
        )

        _save_state(
            state
        )

    active = state.get(
        "active",
        {},
    )

    records = list(
        active.values()
    )

    records.sort(
        key=lambda item: (
            item.get(
                "interest_score",
                0.0,
            )
        ),
        reverse=True,
    )

    return tuple(records)


def get_active_attention_symbols() -> tuple[
    str,
    ...,
]:
    return tuple(
        record["symbol"]
        for record in (
            get_active_attention()
        )
    )


def get_attention_snapshot() -> dict:
    active = (
        get_active_attention()
    )

    state = _load_state()

    return {
        "status": "success",
        "active_count": len(
            active
        ),
        "active": list(
            active
        ),
        "last_demotion": (
            state.get(
                "last_demotion"
            )
        ),
    }


if __name__ == "__main__":
    print(
        json.dumps(
            get_attention_snapshot(),
            indent=2,
        )
    )
