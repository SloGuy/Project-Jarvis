import re

ASSET_ALIASES = {
    "AAPL": ["Apple", "Apple Inc"],
    "TSLA": ["Tesla", "Tesla Inc"],
    "BTC": ["Bitcoin"],
    "ETH": ["Ethereum", "Ether"],
    "XMR": ["Monero"],
    "XRP": ["Ripple", "Ripple Labs"],
    "SPY": ["S&P 500", "SPDR S&P 500 ETF"],
    "QQQ": ["Nasdaq 100", "Nasdaq-100", "Invesco QQQ"],
    "DIA": ["Dow Jones", "Dow Jones Industrial Average"],
}

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import (
    MarketAsset,
    MarketNewsArticle,
    MarketNewsArticleAsset,
)

# Build regex patterns for every asset.
# We match either the ticker symbol or the asset name.

def _build_patterns(assets):
    patterns = {}

    for asset in assets:
        terms = {asset.symbol.upper()} if asset.symbol else set()

        if asset.name:
            terms.add(asset.name.upper())

        for alias in ASSET_ALIASES.get(asset.symbol.upper(), []):
            terms.add(alias.upper())

        escaped = [re.escape(term) for term in terms if term]

        patterns[asset.id] = re.compile(
            r"\b(?:"
            + "|".join(escaped)
            + r")\b",
            re.IGNORECASE,
        )

    return patterns


def link_news_articles():
    session = SessionLocal()

    try:
        assets = session.scalars(
            select(MarketAsset).where(MarketAsset.is_active.is_(True))
        ).all()

        patterns = _build_patterns(assets)

        articles = session.scalars(
            select(MarketNewsArticle).where(
                MarketNewsArticle.processed.is_(False)
            )
        ).all()

        linked = 0
        skipped = 0
        matched_articles = 0

        for article in articles:
            text = f"{article.title or ''} {article.summary or ''}"
            article_matched = False

            for asset in assets:
                pattern = patterns[asset.id]

                if not pattern.search(text):
                    continue

                article_matched = True

                existing = session.scalar(
                    select(MarketNewsArticleAsset).where(
                        MarketNewsArticleAsset.article_id == article.id,
                        MarketNewsArticleAsset.asset_id == asset.id,
                    )
                )

                if existing:
                    skipped += 1
                    continue

                session.add(
                    MarketNewsArticleAsset(
                        article_id=article.id,
                        asset_id=asset.id,
                    )
                )

                linked += 1

            if article_matched:
                matched_articles += 1

            article.processed = True

        session.commit()

        return {
            "articles_checked": len(articles),
            "articles_matched": matched_articles,
            "linked": linked,
            "skipped": skipped,
        }

    finally:
        session.close()
