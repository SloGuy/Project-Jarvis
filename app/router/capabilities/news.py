import re
from typing import Any

from app.market_db.news_queries import get_recent_market_news
from app.router.capabilities.asset import ASSET_ALIASES
from app.router.capabilities.base import RouterCapability


NEWS_PATTERNS = (
    r"\bnews\b",
    r"\bheadline(?:s)?\b",
    r"\barticles?\b",
    r"\blatest\s+news\b",
    r"\brecent\s+news\b",
    r"\bmarket\s+news\b",
)


class NewsCapability(RouterCapability):
    name = "news"
    endpoint = "/market/news"
    patterns = NEWS_PATTERNS

    def _extract_symbol(self, message: str) -> str | None:
        lowered = message.lower()

        for symbol, aliases in ASSET_ALIASES.items():
            for alias in aliases:
                pattern = rf"(?<!\w){re.escape(alias)}(?!\w)"

                if re.search(pattern, lowered):
                    return symbol

        return None

    def match(self, message: str) -> dict[str, Any] | None:
        result = super().match(message)

        if result is None:
            return None

        symbol = self._extract_symbol(message)

        if symbol:
            return {"symbol": symbol}

        return {}

    def execute(
        self,
        symbol: str | None = None,
        **parameters: Any,
    ) -> dict[str, Any]:
        return get_recent_market_news(
            symbol=symbol,
            limit=10,
        )

    def format_response(
        self,
        data: Any,
        symbol: str | None = None,
        **parameters: Any,
    ) -> str:
        if not isinstance(data, dict):
            raise TypeError(
                "News capability expected dictionary data."
            )

        articles = data.get("articles", [])

        if symbol:
            title = f"Latest {symbol} News"
        else:
            title = "Latest Market News"

        lines = [
            title,
            "",
            f"Articles returned: {len(articles)}",
        ]

        if not articles:
            lines.extend(
                [
                    "",
                    "No matching news articles were found.",
                ]
            )

            return "\n".join(lines)

        lines.append("")

        for article in articles[:5]:
            article_title = article.get(
                "title",
                "Untitled article",
            )

            source = article.get(
                "source_name",
                "Unknown source",
            )

            lines.append(
                f"• {article_title}"
            )

            lines.append(
                f"  Source: {source}"
            )

            if article.get("published_at"):
                lines.append(
                    f"  Published: {article['published_at']}"
                )

            lines.append("")

        return "\n".join(lines)
