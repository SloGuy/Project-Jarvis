import json
from datetime import datetime, timezone
from pathlib import Path

from app.market_db.news_finnhub import (
    ingest_company_news,
    ingest_latest_market_news,
)
from app.market_universe import (
    get_market_assets,
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


def _get_permanent_news_symbols() -> set[str]:
    return {
        asset.symbol
        for asset in get_market_assets(
            asset_type="stock",
        )
        if (
            asset.news_enabled
            and asset.tracking_tier
            == "deep"
        )
    }


def _get_deep_selection_symbols() -> set[str]:
    try:
        with DEEP_SELECTION_STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as handle:
            payload = json.load(
                handle
            )

        symbols = payload.get(
            "symbols",
            [],
        )

        if not isinstance(
            symbols,
            list,
        ):
            return set()

        return {
            str(symbol)
            .strip()
            .upper()
            for symbol in symbols
            if str(symbol).strip()
        }

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        return set()


def collect_market_news(
    company_lookback_days: int = 1,
) -> dict:
    permanent_symbols = (
        _get_permanent_news_symbols()
    )

    deep_selection_symbols = (
        _get_deep_selection_symbols()
    )

    company_news_symbols = sorted(
        permanent_symbols
        | deep_selection_symbols
    )

    results = {
        "status": "success",
        "collector_started_at": utc_now(),
        "general_news": None,
        "company_news": None,
        "company_news_symbols": (
            company_news_symbols
        ),
        "permanent_news_symbol_count": len(
            permanent_symbols
        ),
        "deep_selection_symbol_count": len(
            deep_selection_symbols
        ),
        "errors": [],
    }

    try:
        results["general_news"] = (
            ingest_latest_market_news()
        )
    except Exception as error:
        results["status"] = (
            "partial_failure"
        )

        results["errors"].append(
            {
                "source": "general_news",
                "error": str(error),
            }
        )

    try:
        results["company_news"] = (
            ingest_company_news(
                lookback_days=(
                    company_lookback_days
                ),
                symbols=(
                    company_news_symbols
                ),
            )
        )
    except Exception as error:
        results["status"] = (
            "partial_failure"
        )

        results["errors"].append(
            {
                "source": "company_news",
                "error": str(error),
            }
        )

    if (
        results["general_news"]
        is None
        and results["company_news"]
        is None
    ):
        results["status"] = "failed"

    results["collector_finished_at"] = (
        utc_now()
    )

    return results


if __name__ == "__main__":
    print(
        json.dumps(
            collect_market_news(),
            indent=2,
            default=str,
        )
    )
