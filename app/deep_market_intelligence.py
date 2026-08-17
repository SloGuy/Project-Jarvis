import json
from datetime import datetime, timezone
from pathlib import Path

from app.market_db.move_explainer import (
    explain_market_move,
)
from app.market_db.news_queries import (
    get_recent_market_news,
)
from app.market_db.trends import (
    get_asset_trend,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEEP_SELECTION_STATE_FILE = (
    PROJECT_ROOT
    / "runtime"
    / "broad_market_deep_selection.json"
)


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _load_deep_selection() -> dict:
    try:
        with DEEP_SELECTION_STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as handle:
            payload = json.load(
                handle
            )

        if isinstance(payload, dict):
            return payload

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        pass

    return {
        "generated_at": None,
        "count": 0,
        "symbols": [],
        "assets": [],
    }


def _move_available(
    result: dict,
) -> bool:
    return (
        result.get("status")
        == "success"
        and result.get("move")
        is not None
    )


def _trend_available(
    result: dict,
) -> bool:
    return (
        result.get("status")
        == "healthy"
        and result.get(
            "statistics"
        )
        is not None
    )


def _build_readiness(
    *,
    trend_1h: dict,
    trend_24h: dict,
    move_15m: dict,
    move_60m: dict,
    move_24h: dict,
    news: dict,
) -> dict:
    available_move_windows = []

    if _move_available(
        move_15m
    ):
        available_move_windows.append(
            "15m"
        )

    if _move_available(
        move_60m
    ):
        available_move_windows.append(
            "60m"
        )

    if _move_available(
        move_24h
    ):
        available_move_windows.append(
            "24h"
        )

    trend_windows = []

    if _trend_available(
        trend_1h
    ):
        trend_windows.append(
            "1h"
        )

    if _trend_available(
        trend_24h
    ):
        trend_windows.append(
            "24h"
        )

    news_count = int(
        news.get(
            "count",
            0,
        )
    )

    if (
        len(available_move_windows) >= 2
        and len(trend_windows) >= 1
    ):
        level = "established"

    elif (
        available_move_windows
        or trend_windows
    ):
        level = "developing"

    else:
        level = "initializing"

    return {
        "level": level,
        "trend_windows_available": (
            trend_windows
        ),
        "move_windows_available": (
            available_move_windows
        ),
        "linked_news_count": (
            news_count
        ),
        "has_linked_news": (
            news_count > 0
        ),
    }


def build_deep_intelligence(
    *,
    symbol: str,
    selection_context: dict | None = None,
) -> dict:
    normalized_symbol = (
        symbol
        .strip()
        .upper()
    )

    trend_1h = get_asset_trend(
        symbol=normalized_symbol,
        hours=1,
        chart_points=60,
    )

    trend_24h = get_asset_trend(
        symbol=normalized_symbol,
        hours=24,
        chart_points=120,
    )

    move_15m = explain_market_move(
        symbol=normalized_symbol,
        comparison_minutes=15,
        news_lookback_hours=72,
        news_limit=10,
    )

    move_60m = explain_market_move(
        symbol=normalized_symbol,
        comparison_minutes=60,
        news_lookback_hours=72,
        news_limit=10,
    )

    move_24h = explain_market_move(
        symbol=normalized_symbol,
        comparison_minutes=1440,
        news_lookback_hours=72,
        news_limit=10,
    )

    news = get_recent_market_news(
        symbol=normalized_symbol,
        limit=10,
    )

    readiness = _build_readiness(
        trend_1h=trend_1h,
        trend_24h=trend_24h,
        move_15m=move_15m,
        move_60m=move_60m,
        move_24h=move_24h,
        news=news,
    )

    return {
        "status": "success",
        "generated_at": utc_now(),
        "symbol": normalized_symbol,
        "selection_context": (
            selection_context
            or {}
        ),
        "readiness": readiness,
        "trend": {
            "1h": trend_1h,
            "24h": trend_24h,
        },
        "moves": {
            "15m": move_15m,
            "60m": move_60m,
            "24h": move_24h,
        },
        "news": news,
    }


def build_selected_deep_intelligence() -> dict:
    selection = (
        _load_deep_selection()
    )

    assets = selection.get(
        "assets",
        [],
    )

    reports = []

    for asset in assets:
        symbol = asset.get(
            "symbol"
        )

        if not symbol:
            continue

        reports.append(
            build_deep_intelligence(
                symbol=symbol,
                selection_context=asset,
            )
        )

    readiness_counts = {
        "initializing": 0,
        "developing": 0,
        "established": 0,
    }

    for report in reports:
        level = (
            report
            .get(
                "readiness",
                {},
            )
            .get(
                "level"
            )
        )

        if level in readiness_counts:
            readiness_counts[
                level
            ] += 1

    return {
        "status": "success",
        "generated_at": utc_now(),
        "selection_generated_at": (
            selection.get(
                "generated_at"
            )
        ),
        "selected_count": len(
            assets
        ),
        "report_count": len(
            reports
        ),
        "readiness_counts": (
            readiness_counts
        ),
        "reports": reports,
    }


if __name__ == "__main__":
    print(
        json.dumps(
            build_selected_deep_intelligence(),
            indent=2,
            default=str,
        )
    )
