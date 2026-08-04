import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone

import requests
from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import (
    MarketAsset,
    MarketNewsArticle,
    MarketNewsArticleAsset,
)
from app.market_db.news_linker import link_news_articles
from decimal import Decimal


FINNHUB_MARKET_NEWS_URL = "https://finnhub.io/api/v1/news"
FINNHUB_COMPANY_NEWS_URL = (
    "https://finnhub.io/api/v1/company-news"
)

NEWS_PROVIDER = "Finnhub"
ARTICLE_TYPE = "market"
NEWS_CATEGORY = "general"
REQUEST_TIMEOUT_SECONDS = 30


def _get_api_key() -> str:
    api_key = os.getenv("FINNHUB_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("FINNHUB_API_KEY is not configured.")

    return api_key


def _parse_timestamp(value) -> datetime | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None

    if timestamp <= 0:
        return None

    try:
        return datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc,
        )
    except (OverflowError, OSError, ValueError):
        return None


def _normalize_symbol(value: str | None) -> str | None:
    if not value:
        return None

    symbol = value.strip().upper()

    if not symbol or len(symbol) > 20:
        return None

    allowed_characters = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
    )

    if any(
        character not in allowed_characters
        for character in symbol
    ):
        return None

    return symbol


def _extract_symbols(row: dict) -> list[str]:
    raw_related = str(row.get("related") or "").strip()

    if not raw_related:
        return []

    symbols = []
    seen = set()

    for item in raw_related.split(","):
        symbol = _normalize_symbol(item)

        if symbol is None or symbol in seen:
            continue

        seen.add(symbol)
        symbols.append(symbol)

    return symbols


def _build_content_hash(
    title: str,
    url: str,
    published_at: datetime,
) -> str:
    hash_input = "|".join(
        (
            title.strip().lower(),
            url.strip().lower(),
            published_at.isoformat(),
        )
    )

    return hashlib.sha256(
        hash_input.encode("utf-8")
    ).hexdigest()


def _fetch_market_news(
    api_key: str,
    category: str = NEWS_CATEGORY,
) -> list[dict]:
    response = requests.get(
        FINNHUB_MARKET_NEWS_URL,
        params={
            "category": category,
            "token": api_key,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Finnhub returned HTTP {response.status_code}."
        )

    try:
        payload = response.json()
    except requests.JSONDecodeError as error:
        raise RuntimeError(
            "Finnhub returned invalid JSON."
        ) from error

    if isinstance(payload, dict):
        message = (
            payload.get("error")
            or payload.get("message")
        )

        raise RuntimeError(
            message or "Unexpected Finnhub news response."
        )

    if not isinstance(payload, list):
        raise RuntimeError(
            "Finnhub news response was not a list."
        )

    return payload


def _fetch_company_news(
    api_key: str,
    symbol: str,
    from_date: date,
    to_date: date,
) -> list[dict]:
    response = requests.get(
        FINNHUB_COMPANY_NEWS_URL,
        params={
            "symbol": symbol,
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "token": api_key,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Finnhub company news for {symbol} returned HTTP "
            f"{response.status_code}."
        )

    try:
        payload = response.json()
    except requests.JSONDecodeError as error:
        raise RuntimeError(
            f"Finnhub company news for {symbol} returned invalid JSON."
        ) from error

    if isinstance(payload, dict):
        message = payload.get("error") or payload.get("message")
        raise RuntimeError(
            message
            or f"Unexpected Finnhub company news response for {symbol}."
        )

    if not isinstance(payload, list):
        raise RuntimeError(
            f"Finnhub company news for {symbol} was not a list."
        )

    return payload


def _get_active_stock_assets(session) -> list[MarketAsset]:
    return list(
        session.scalars(
            select(MarketAsset)
            .where(
                MarketAsset.is_active.is_(True),
                MarketAsset.asset_type == "stock",
            )
            .order_by(MarketAsset.symbol)
        ).all()
    )


def preview_company_news(
    lookback_days: int = 3,
) -> dict:
    api_key = _get_api_key()
    to_date = datetime.now(timezone.utc).date()
    from_date = to_date - timedelta(days=lookback_days)

    results = []

    with SessionLocal() as session:
        assets = _get_active_stock_assets(session)

        for asset in assets:
            rows = _fetch_company_news(
                api_key=api_key,
                symbol=asset.symbol,
                from_date=from_date,
                to_date=to_date,
            )

            results.append(
                {
                    "symbol": asset.symbol,
                    "articles_received": len(rows),
                }
            )

    return {
        "status": "success",
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "assets_checked": len(results),
        "results": results,
    }


def _get_or_create_asset(
    session,
    symbol: str,
) -> MarketAsset:
    asset = session.scalar(
        select(MarketAsset).where(
            MarketAsset.symbol == symbol,
            MarketAsset.asset_type == "stock",
        )
    )

    if asset is not None:
        return asset

    asset = MarketAsset(
        symbol=symbol,
        asset_type="stock",
        provider_id=symbol,
        is_active=True,
    )

    session.add(asset)
    session.flush()

    return asset


def _find_existing_article(
    session,
    provider_article_id,
    content_hash: str,
) -> MarketNewsArticle | None:
    normalized_provider_id = (
        str(provider_article_id)
        if provider_article_id is not None
        else None
    )

    if normalized_provider_id:
        existing_by_provider_id = session.scalar(
            select(MarketNewsArticle).where(
                MarketNewsArticle.provider == NEWS_PROVIDER,
                MarketNewsArticle.provider_article_id
                == normalized_provider_id,
            )
        )

        if existing_by_provider_id is not None:
            return existing_by_provider_id

    return session.scalar(
        select(MarketNewsArticle).where(
            MarketNewsArticle.content_hash == content_hash
        )
    )


def _ensure_asset_link(
    session,
    article: MarketNewsArticle,
    asset: MarketAsset,
) -> bool:
    existing_link = session.scalar(
        select(MarketNewsArticleAsset).where(
            MarketNewsArticleAsset.article_id == article.id,
            MarketNewsArticleAsset.asset_id == asset.id,
        )
    )

    if existing_link is not None:
        return False

    session.add(
        MarketNewsArticleAsset(
            article_id=article.id,
            asset_id=asset.id,
            link_type="company_feed",
            linked_by="finnhub_company_feed",
            match_reason=(
                f"Returned by Finnhub company-news "
                f"endpoint for {asset.symbol}"
            ),
            matched_text=asset.symbol,
            confidence_score=Decimal("0.6500"),
        )
    )

    return True


def ingest_company_news(
    lookback_days: int = 3,
) -> dict:
    api_key = _get_api_key()
    to_date = datetime.now(timezone.utc).date()
    from_date = to_date - timedelta(days=lookback_days)

    inserted = 0
    duplicates = 0
    invalid = 0
    asset_links_created = 0
    results = []

    with SessionLocal() as session:
        assets = _get_active_stock_assets(session)

        for asset in assets:
            rows = _fetch_company_news(
                api_key=api_key,
                symbol=asset.symbol,
                from_date=from_date,
                to_date=to_date,
            )

            symbol_inserted = 0
            symbol_duplicates = 0
            symbol_links = 0

            for row in rows:
                title = str(
                    row.get("headline") or ""
                ).strip()

                url = str(
                    row.get("url") or ""
                ).strip()

                published_at = _parse_timestamp(
                    row.get("datetime")
                )

                if not title or not url or published_at is None:
                    invalid += 1
                    continue

                content_hash = _build_content_hash(
                    title=title,
                    url=url,
                    published_at=published_at,
                )

                article = session.scalar(
                    select(MarketNewsArticle).where(
                        MarketNewsArticle.content_hash
                        == content_hash
                    )
                )

                if article is None:
                    provider_article_id = row.get("id")

                    article = MarketNewsArticle(
                        provider=NEWS_PROVIDER,
                        provider_article_id=(
                            str(provider_article_id)
                            if provider_article_id is not None
                            else None
                        ),
                        title=title,
                        summary=(
                            str(row.get("summary")).strip()
                            if row.get("summary")
                            else None
                        ),
                        url=url,
                        image_url=(
                            str(row.get("image")).strip()
                            if row.get("image")
                            else None
                        ),
                        source_name=(
                            str(row.get("source")).strip()
                            if row.get("source")
                            else None
                        ),
                        author=None,
                        article_type="company",
                        published_at=published_at,
                        content_hash=content_hash,
                        raw_payload=row,
                    )

                    session.add(article)
                    session.flush()

                    inserted += 1
                    symbol_inserted += 1
                else:
                    duplicates += 1
                    symbol_duplicates += 1

                if _ensure_asset_link(
                    session=session,
                    article=article,
                    asset=asset,
                ):
                    asset_links_created += 1
                    symbol_links += 1

            results.append(
                {
                    "symbol": asset.symbol,
                    "articles_received": len(rows),
                    "inserted": symbol_inserted,
                    "duplicates": symbol_duplicates,
                    "asset_links_created": symbol_links,
                }
            )

        session.commit()

    linking_result = link_news_articles()

    return {
        "status": "success",
        "provider": NEWS_PROVIDER,
        "article_type": "company",
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "assets_checked": len(results),
        "inserted": inserted,
        "duplicates_skipped": duplicates,
        "invalid_skipped": invalid,
        "asset_links_created": asset_links_created,
        "results": results,
        "news_processing": linking_result,
    }


def ingest_latest_market_news(
    category: str = NEWS_CATEGORY,
) -> dict:
    api_key = _get_api_key()

    rows = _fetch_market_news(
        api_key=api_key,
        category=category,
    )

    inserted = 0
    duplicates = 0
    invalid = 0
    asset_links_created = 0

    with SessionLocal() as session:
        for row in rows:
            title = str(
                row.get("headline") or ""
            ).strip()

            url = str(
                row.get("url") or ""
            ).strip()

            published_at = _parse_timestamp(
                row.get("datetime")
            )

            if not title or not url or published_at is None:
                invalid += 1
                continue

            content_hash = _build_content_hash(
                title=title,
                url=url,
                published_at=published_at,
            )

            provider_article_id = row.get("id")

            existing_article = _find_existing_article(
                session=session,
                provider_article_id=provider_article_id,
                content_hash=content_hash,
            )

            if existing_article is not None:
                duplicates += 1
                continue

            article = MarketNewsArticle(
                provider=NEWS_PROVIDER,
                provider_article_id=(
                    str(provider_article_id)
                    if provider_article_id is not None
                    else None
                ),
                title=title,
                summary=(
                    str(row.get("summary")).strip()
                    if row.get("summary")
                    else None
                ),
                url=url,
                image_url=(
                    str(row.get("image")).strip()
                    if row.get("image")
                    else None
                ),
                source_name=(
                    str(row.get("source")).strip()
                    if row.get("source")
                    else None
                ),
                author=None,
                article_type=ARTICLE_TYPE,
                published_at=published_at,
                content_hash=content_hash,
                raw_payload=row,
            )

            session.add(article)
            session.flush()

            for symbol in _extract_symbols(row):
                asset = _get_or_create_asset(
                    session=session,
                    symbol=symbol,
                )

                session.add(
                    MarketNewsArticleAsset(
                        article_id=article.id,
                        asset_id=asset.id,
                    )
                )

                asset_links_created += 1

            inserted += 1

        session.commit()

    linking_result = link_news_articles()

    return {
        "status": "success",
        "provider": NEWS_PROVIDER,
        "article_type": ARTICLE_TYPE,
        "category": category,
        "articles_received": len(rows),
        "inserted": inserted,
        "duplicates_skipped": duplicates,
        "invalid_skipped": invalid,
        "asset_links_created": asset_links_created,
        "news_processing": linking_result,
    }


if __name__ == "__main__":
    try:
        result = ingest_latest_market_news()
    except Exception as error:
        result = {
            "status": "failed",
            "provider": NEWS_PROVIDER,
            "error": str(error),
        }

    print(
        json.dumps(
            result,
            indent=2,
            default=str,
        )
    )
