from datetime import datetime, timezone

from app.market_db.moves import get_latest_market_moves
from app.market_db.news_queries import get_recent_market_news


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def _rank_news(
    articles: list[dict],
    latest_observed_at: str | None,
    news_lookback_hours: int,
) -> list[dict]:
    latest_time = _parse_datetime(latest_observed_at)

    if latest_time is None:
        return []

    ranked = []

    for article in articles:
        published_at = _parse_datetime(
            article.get("published_at")
        )

        if published_at is None:
            continue

        age_hours = (
            latest_time - published_at
        ).total_seconds() / 3600

        if age_hours < 0:
            continue

        if age_hours > news_lookback_hours:
            continue

        if age_hours <= 6:
            relevance = "high"
        elif age_hours <= 24:
            relevance = "medium"
        else:
            relevance = "low"

        ranked.append(
            {
                **article,
                "hours_before_latest_observation": round(
                    age_hours,
                    2,
                ),
                "timing_relevance": relevance,
            }
        )

    ranked.sort(
        key=lambda article: article[
            "hours_before_latest_observation"
        ]
    )

    return ranked


def explain_market_move(
    symbol: str,
    comparison_minutes: int = 1440,
    news_lookback_hours: int = 72,
    news_limit: int = 25,
) -> dict:
    normalized_symbol = symbol.strip().upper()

    move_result = get_latest_market_moves(
        symbol=normalized_symbol,
        comparison_minutes=comparison_minutes,
        minimum_move_percent=0.0,
        limit=1,
    )

    if not move_result["moves"]:
        return {
            "status": "unavailable",
            "symbol": normalized_symbol,
            "summary": (
                "No comparable price observations are available "
                "for this asset."
            ),
            "move": None,
            "related_news_count": 0,
            "related_news": [],
        }

    move = move_result["moves"][0]

    news_result = get_recent_market_news(
        symbol=normalized_symbol,
        limit=news_limit,
    )

    related_news = _rank_news(
        articles=news_result["articles"],
        latest_observed_at=move["latest_observed_at"],
        news_lookback_hours=news_lookback_hours,
    )

    interval_move_percent = (
        move["interval_change_percent"] or 0.0
    )
    provider_move_percent = move.get(
        "provider_change_percent"
    )

    if related_news:
        summary = (
            f"{normalized_symbol} moved "
            f"{interval_move_percent:+.3f}% over the requested interval. "
            f"{len(related_news)} linked news article"
            f"{'s' if len(related_news) != 1 else ''} "
            f"{'were' if len(related_news) != 1 else 'was'} "
            "published within the selected lookback window."
        )
        confidence = (
            "medium"
            if related_news[0]["timing_relevance"] in {
                "high",
                "medium",
            }
            else "low"
        )
    else:
        summary = (
            f"{normalized_symbol} moved "
            f"{interval_move_percent:+.3f}% over the requested interval, "
            "but no linked news articles were found within the "
            "selected lookback window."
        )
        confidence = "insufficient"

    provider_context = None

    if provider_move_percent is not None:
        move_difference = abs(
            provider_move_percent - interval_move_percent
        )

        if (
            abs(interval_move_percent) < 0.01
            and abs(provider_move_percent) >= 0.25
        ):
            provider_context = (
                "Stored observations show little net price change "
                f"over the selected interval "
                f"({interval_move_percent:+.3f}%). However, "
                f"{move['provider']} currently reports a session "
                f"change of {provider_move_percent:+.3f}%. "
                "These figures use different comparison windows."
            )
        elif move_difference >= 0.25:
            provider_context = (
                f"Stored observations show a change of "
                f"{interval_move_percent:+.3f}% over the selected "
                f"interval, while {move['provider']} reports a "
                f"current session change of "
                f"{provider_move_percent:+.3f}%. "
                "These figures use different comparison windows."
            )
        else:
            provider_context = (
                f"The data provider reports a current session "
                f"change of {provider_move_percent:+.3f}%."
            )

    return {
        "status": "success",
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "symbol": normalized_symbol,
        "summary": summary,
        "provider_context": provider_context,
        "confidence": confidence,
        "comparison_minutes": comparison_minutes,
        "news_lookback_hours": news_lookback_hours,
        "move": move,
        "related_news_count": len(related_news),
        "related_news": related_news,
        "disclaimer": (
            "Timing and asset linkage indicate correlation only. "
            "They do not prove that a news article caused the move."
        ),
    }
