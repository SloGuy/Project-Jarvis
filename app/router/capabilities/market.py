from typing import Any

from app.market_db.intelligence import get_market_intelligence
from app.router.capabilities.base import RouterCapability


class MarketCapability(RouterCapability):
    name = "market"
    endpoint = "/market/intelligence"

    patterns = (
        r"\bwhat(?:'s| is)\s+happening\s+in\s+the\s+market\b",
        r"\bmarket\s+overview\b",
        r"\bmarket\s+status\b",
        r"\bhow\s+are\s+the\s+markets\b",
        r"\bshow\s+me\s+the\s+market\b",
        r"\bcurrent\s+market\s+summary\b",
        r"\bgive\s+me\s+(a\s+)?market\s+brief\b",
    )

    def execute(self) -> dict[str, Any]:
        return get_market_intelligence(
            comparison_minutes=15,
            mover_threshold_percent=0.25,
            alert_limit=10,
        )

    def format_response(self, data: Any) -> str:
        if not isinstance(data, dict):
            raise TypeError("Market capability expected dictionary data.")

        database = data.get("database", {})
        movers = data.get("movers", {})
        alerts = data.get("alerts", {})
        news = data.get("recent_news", {})
        insights = data.get("insights", [])

        lines = [
            "Market Intelligence",
            "",
            f"Status: {data.get('status', 'unknown').title()}",
            f"Summary: {data.get('summary', 'No summary available.')}",
            "",
            (
                f"Tracked assets: "
                f"{database.get('active_assets', 0)}"
            ),
            (
                f"Stored observations: "
                f"{database.get('total_observations', 0):,}"
            ),
            (
                f"Recent movers: "
                f"{movers.get('returned', 0)}"
            ),
            (
                f"Recent alerts: "
                f"{alerts.get('returned', 0)}"
            ),
            (
                f"Stored news articles: "
                f"{database.get('news_articles', 0)}"
            ),
            (
                f"Linked news items: "
                f"{database.get('news_asset_links', 0)}"
            ),
        ]

        news_count = news.get("count", 0)

        if news_count:
            lines.append(f"Recent news returned: {news_count}")

        if insights:
            lines.extend(["", "Insights:"])

            for insight in insights[:5]:
                lines.append(f"- {insight}")

        return "\n".join(lines)
