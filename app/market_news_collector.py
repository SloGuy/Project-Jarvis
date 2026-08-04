import json
from datetime import datetime, timezone

from app.market_db.news_finnhub import (
    ingest_company_news,
    ingest_latest_market_news,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def collect_market_news(
    company_lookback_days: int = 1,
) -> dict:
    results = {
        "status": "success",
        "collector_started_at": utc_now(),
        "general_news": None,
        "company_news": None,
        "errors": [],
    }

    try:
        results["general_news"] = ingest_latest_market_news()
    except Exception as error:
        results["status"] = "partial_failure"
        results["errors"].append(
            {
                "source": "general_news",
                "error": str(error),
            }
        )

    try:
        results["company_news"] = ingest_company_news(
            lookback_days=company_lookback_days,
        )
    except Exception as error:
        results["status"] = "partial_failure"
        results["errors"].append(
            {
                "source": "company_news",
                "error": str(error),
            }
        )

    if (
        results["general_news"] is None
        and results["company_news"] is None
    ):
        results["status"] = "failed"

    results["collector_finished_at"] = utc_now()

    return results


if __name__ == "__main__":
    print(
        json.dumps(
            collect_market_news(),
            indent=2,
            default=str,
        )
    )
