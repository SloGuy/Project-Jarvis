from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import (
    MarketAsset,
    MarketNewsArticle,
    MarketNewsArticleAsset,
)


def get_recent_market_news(
    symbol: str | None = None,
    limit: int = 25,
) -> dict:
    safe_limit = max(1, min(limit, 100))

    with SessionLocal() as session:
        statement = (
            select(
                MarketNewsArticle,
                MarketAsset.symbol,
            )
            .join(
                MarketNewsArticleAsset,
                MarketNewsArticleAsset.article_id
                == MarketNewsArticle.id,
            )
            .join(
                MarketAsset,
                MarketAsset.id
                == MarketNewsArticleAsset.asset_id,
            )
            .order_by(
                MarketNewsArticle.published_at.desc()
            )
            .limit(safe_limit)
        )

        normalized_symbol = None

        if symbol:
            normalized_symbol = symbol.strip().upper()

            statement = statement.where(
                MarketAsset.symbol == normalized_symbol
            )

        rows = session.execute(statement).all()

        articles = []

        for article, asset_symbol in rows:
            articles.append(
                {
                    "id": article.id,
                    "symbol": asset_symbol,
                    "title": article.title,
                    "summary": article.summary,
                    "source_name": article.source_name,
                    "url": article.url,
                    "published_at": (
                        article.published_at.isoformat()
                        if article.published_at
                        else None
                    ),
                    "provider": article.provider,
                    "article_type": article.article_type,
                }
            )

        return {
            "status": "success",
            "symbol": normalized_symbol,
            "count": len(articles),
            "limit": safe_limit,
            "articles": articles,
        }
