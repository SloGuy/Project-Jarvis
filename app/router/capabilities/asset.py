import re
from typing import Any

from app.market_db.alerts import get_recent_alerts
from app.market_db.intelligence import get_market_intelligence
from app.market_db.moves import get_latest_market_moves
from app.market_db.news_queries import get_recent_market_news
from app.router.capabilities.base import RouterCapability


ASSET_ALIASES = {
    "BTC": ("btc", "bitcoin"),
    "ETH": ("eth", "ethereum"),
    "XMR": ("xmr", "monero"),
    "XRP": ("xrp", "ripple"),
    "AAPL": ("aapl", "apple"),
    "TSLA": ("tsla", "tesla"),
    "SPY": ("spy", "s&p 500", "s&p500"),
    "QQQ": ("qqq", "nasdaq 100", "nasdaq-100"),
    "DIA": ("dia", "dow jones", "dow"),
}


ASSET_NAMES = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "XMR": "Monero",
    "XRP": "XRP",
    "AAPL": "Apple",
    "TSLA": "Tesla",
    "SPY": "SPDR S&P 500 ETF",
    "QQQ": "Invesco QQQ",
    "DIA": "SPDR Dow Jones ETF",
}


REASONING_PATTERNS = (
    r"\bwhy\b",
    r"\bshould\s+i\b",
    r"\bwhat\s+do\s+you\s+think\b",
    r"\bpredict\b",
    r"\bforecast\b",
    r"\boutlook\b",
    r"\bwill\b",
    r"\bcould\b",
)


DIRECT_ASSET_PATTERNS = (
    r"\bwhat(?:'s| is)\b.*\b(?:trading at|price|at)\b",
    r"\bhow(?:'s| is)\b.*\b(?:doing|performing)\b",
    r"\bgive\s+me\b.*\b(?:status|update|report)\b",
    r"\btell\s+me\s+about\b",
    r"\bshow\s+me\b.*\b(?:price|status|update)\b",
    r"\bcurrent\b.*\bprice\b",
)


class AssetCapability(RouterCapability):
    name = "asset"
    endpoint = "/market/intelligence"
    patterns: tuple[str, ...] = ()

    def _extract_symbol(self, message: str) -> str | None:
        for symbol, aliases in ASSET_ALIASES.items():
            for alias in aliases:
                pattern = rf"(?<!\w){re.escape(alias)}(?!\w)"

                if re.search(pattern, message):
                    return symbol

        return None

    def match(self, message: str) -> dict[str, Any] | None:
        if any(
            re.search(pattern, message)
            for pattern in REASONING_PATTERNS
        ):
            return None

        symbol = self._extract_symbol(message)

        if symbol is None:
            return None

        if not any(
            re.search(pattern, message)
            for pattern in DIRECT_ASSET_PATTERNS
        ):
            return None

        return {
            "symbol": symbol,
        }

    def execute(
        self,
        symbol: str,
        **parameters: Any,
    ) -> dict[str, Any]:
        normalized_symbol = symbol.upper().strip()

        intelligence = get_market_intelligence(
            comparison_minutes=15,
            mover_threshold_percent=0.0,
            alert_limit=100,
        )

        market = intelligence.get("market", {})
        all_assets = (
            market.get("stocks", [])
            + market.get("crypto", [])
        )

        asset = next(
            (
                item
                for item in all_assets
                if item.get("symbol") == normalized_symbol
            ),
            None,
        )

        moves = get_latest_market_moves(
            symbol=normalized_symbol,
            limit=1,
            minimum_move_percent=0.0,
            comparison_minutes=15,
        )

        all_alerts = get_recent_alerts(limit=100)

        asset_alerts = [
            alert
            for alert in all_alerts.get("alerts", [])
            if alert.get("symbol") == normalized_symbol
        ][:3]

        news = get_recent_market_news(
            symbol=normalized_symbol,
            limit=3,
        )

        return {
            "symbol": normalized_symbol,
            "name": ASSET_NAMES.get(
                normalized_symbol,
                normalized_symbol,
            ),
            "asset": asset,
            "moves": moves,
            "alerts": {
                "returned": len(asset_alerts),
                "alerts": asset_alerts,
            },
            "news": news,
            "market_status": intelligence.get("status"),
            "generated_at": intelligence.get("generated_at"),
        }

    def _format_price(self, price: float | None) -> str:
        if price is None:
            return "Unavailable"

        if price < 10:
            return f"${price:,.4f}"

        return f"${price:,.2f}"

    def _format_percent(
        self,
        value: float | None,
    ) -> str:
        if value is None:
            return "Unavailable"

        return f"{value:+.2f}%"

    def format_response(
        self,
        data: Any,
        symbol: str,
        **parameters: Any,
    ) -> str:
        if not isinstance(data, dict):
            raise TypeError(
                "Asset capability expected dictionary data."
            )

        asset = data.get("asset")
        asset_name = data.get("name", symbol)

        if not isinstance(asset, dict):
            return (
                f"{asset_name} ({symbol})\n\n"
                "No current market observation is available."
            )

        moves = data.get("moves", {}).get("moves", [])
        latest_move = moves[0] if moves else None

        alerts = data.get("alerts", {})
        news = data.get("news", {})
        articles = news.get("articles", [])

        lines = [
            f"{asset_name} ({symbol})",
            "",
            (
                f"Price: "
                f"{self._format_price(asset.get('price_usd'))}"
            ),
            (
                "Provider change: "
                f"{self._format_percent(
                    asset.get('provider_change_percent')
                )}"
            ),
        ]

        if latest_move:
            lines.append(
                (
                    "15-minute move: "
                    f"{self._format_percent(
                        latest_move.get(
                            'interval_change_percent'
                        )
                    )} "
                    f"({latest_move.get('direction', 'unknown')})"
                )
            )
        else:
            lines.append(
                "15-minute move: No comparison available"
            )

        lines.extend(
            [
                f"Data source: {asset.get('provider', 'Unknown')}",
                f"Last observed: {asset.get('observed_at', 'Unknown')}",
                (
                    f"Recent alerts: "
                    f"{alerts.get('returned', 0)}"
                ),
                (
                    f"Linked news returned: "
                    f"{news.get('count', 0)}"
                ),
            ]
        )

        if articles:
            lines.extend(["", "Latest linked news:"])

            for article in articles[:2]:
                title = article.get(
                    "title",
                    "Untitled article",
                )
                source = article.get(
                    "source_name",
                    "Unknown source",
                )

                lines.append(f"- {title} — {source}")

        return "\n".join(lines)
