import re
from decimal import Decimal
from app.market_universe import get_news_aliases

ASSET_ALIASES = get_news_aliases()

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
        symbol = asset.symbol.upper() if asset.symbol else None
        asset_patterns = []

        if symbol:
            asset_patterns.append(
                {
                    "pattern": re.compile(
                        rf"\b{re.escape(symbol)}\b",
                        re.IGNORECASE,
                    ),
                    "link_type": "ticker_match",
                    "matched_text": symbol,
                    "confidence_score": Decimal("0.9500"),
                }
            )

        if asset.name:
            asset_patterns.append(
                {
                    "pattern": re.compile(
                        rf"\b{re.escape(asset.name)}\b",
                        re.IGNORECASE,
                    ),
                    "link_type": "asset_name_match",
                    "matched_text": asset.name,
                    "confidence_score": Decimal("0.9000"),
                }
            )

        for alias in ASSET_ALIASES.get(symbol or "", []):
            asset_patterns.append(
                {
                    "pattern": re.compile(
                        rf"\b{re.escape(alias)}\b",
                        re.IGNORECASE,
                    ),
                    "link_type": "alias_match",
                    "matched_text": alias,
                    "confidence_score": Decimal("0.8000"),
                }
            )

        patterns[asset.id] = asset_patterns

    return patterns


def link_news_articles():
    session = SessionLocal()

    try:
        assets = session.scalars(
            select(MarketAsset).where(
                MarketAsset.is_active.is_(True)
            )
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
                matched_pattern = None

                for candidate in patterns[asset.id]:
                    if candidate["pattern"].search(text):
                        matched_pattern = candidate
                        break

                if matched_pattern is None:
                    continue

                article_matched = True

                existing = session.scalar(
                    select(MarketNewsArticleAsset).where(
                        MarketNewsArticleAsset.article_id
                        == article.id,
                        MarketNewsArticleAsset.asset_id
                        == asset.id,
                    )
                )

                if existing:
                    if existing.linked_by == "legacy":
                        existing.link_type = matched_pattern[
                            "link_type"
                        ]
                        existing.linked_by = (
                            "deterministic_linker"
                        )
                        existing.match_reason = (
                            f"Matched "
                            f"{matched_pattern['link_type']} "
                            f"for {asset.symbol}"
                        )
                        existing.matched_text = matched_pattern[
                            "matched_text"
                        ]
                        existing.confidence_score = (
                            matched_pattern[
                                "confidence_score"
                            ]
                        )

                    skipped += 1
                    continue

                session.add(
                    MarketNewsArticleAsset(
                        article_id=article.id,
                        asset_id=asset.id,
                        link_type=matched_pattern["link_type"],
                        linked_by="deterministic_linker",
                        match_reason=(
                            f"Matched "
                            f"{matched_pattern['link_type']} "
                            f"for {asset.symbol}"
                        ),
                        matched_text=matched_pattern[
                            "matched_text"
                        ],
                        confidence_score=matched_pattern[
                            "confidence_score"
                        ],
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
