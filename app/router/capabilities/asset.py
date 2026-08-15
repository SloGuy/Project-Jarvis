import re
from datetime import datetime
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
    "NVDA": ("nvda", "nvidia"),
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
    "NVDA": "NVIDIA",
    "SPY": "SPDR S&P 500 ETF",
    "QQQ": "Invesco QQQ",
    "DIA": "SPDR Dow Jones ETF",
}


REASONING_PATTERNS = (
    r"\bwhy\b",
    r"\bshould\s+i\b",
    r"\bwhat\s+do\s+you\s+think\b",
    r"\bpredict(?:ion)?\b",
    r"\bforecast\b",
    r"\boutlook\b",
    r"\bwill\b",
    r"\bcould\b",
    r"\bwould\b",
    r"\bbuy\b",
    r"\bsell\b",
    r"\binvest\b",
)


DIRECT_ASSET_PATTERNS = (
    r"\bwhat(?:'s| is)\b.*\b(?:trading at|price|at)\b",
    r"\bhow(?:'s| is)\b.*\b(?:doing|performing)\b",
    r"\bgive\s+me\b.*\b(?:status|update|report)\b",
    r"\btell\s+me\s+about\b",
    r"\bshow\s+me\b.*\b(?:price|status|update|report)\b",
    r"\bcurrent\b.*\bprice\b",
    r"\blatest\b.*\b(?:price|status|update)\b",
    r"\bprice\s+of\b",
    r"\bupdate\s+on\b",
    r"\bstatus\s+of\b",
)


class AssetCapability(RouterCapability):
    name = "asset"
    endpoint = "/market/intelligence"
    patterns: tuple[str, ...] = ()

    def _normalize_message(self, message: str) -> str:
        return " ".join(message.lower().strip().split())

    def _extract_symbol(self, message: str) -> str | None:
        normalized_message = self._normalize_message(message)

        for symbol, aliases in ASSET_ALIASES.items():
            ticker_pattern = rf"(?<!\w)\${re.escape(symbol.lower())}(?!\w)"

            if re.search(ticker_pattern, normalized_message):
                return symbol

            for alias in aliases:
                pattern = rf"(?<!\w){re.escape(alias)}(?!\w)"

                if re.search(pattern, normalized_message):
                    return symbol

        return None

    def match(self, message: str) -> dict[str, Any] | None:
        if not isinstance(message, str):
            return None

        normalized_message = self._normalize_message(message)

        if not normalized_message:
            return None

        if any(
            re.search(pattern, normalized_message)
            for pattern in REASONING_PATTERNS
        ):
            return None

        symbol = self._extract_symbol(normalized_message)

        if symbol is None:
            return None

        if not any(
            re.search(pattern, normalized_message)
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
        stocks = market.get("stocks", [])
        crypto = market.get("crypto", [])

        all_assets = [
            item
            for item in stocks + crypto
            if isinstance(item, dict)
        ]

        asset = next(
            (
                item
                for item in all_assets
                if str(item.get("symbol", "")).upper()
                == normalized_symbol
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
            if str(alert.get("symbol", "")).upper()
            == normalized_symbol
        ][:3]

        news = get_recent_market_news(
            symbol=normalized_symbol,
            limit=3,
        )

        return {
            "status": (
                "success"
                if isinstance(asset, dict)
                else "no_current_observation"
            ),
            "symbol": normalized_symbol,
            "name": ASSET_NAMES.get(
                normalized_symbol,
                normalized_symbol,
            ),
            "available": isinstance(asset, dict),
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

    def _format_price(self, price: Any) -> str:
        try:
            numeric_price = float(price)
        except (TypeError, ValueError):
            return "Unavailable"

        if numeric_price < 0:
            return "Unavailable"

        if numeric_price < 0.01:
            return f"${numeric_price:,.8f}"

        if numeric_price < 1:
            return f"${numeric_price:,.6f}"

        if numeric_price < 10:
            return f"${numeric_price:,.4f}"

        return f"${numeric_price:,.2f}"

    def _format_percent(self, value: Any) -> str:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return "Unavailable"

        return f"{numeric_value:+.2f}%"

    def _format_timestamp(self, value: Any) -> str:
        if not value:
            return "Unknown"

        if isinstance(value, datetime):
            return value.isoformat()

        return str(value)

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

        normalized_symbol = symbol.upper().strip()
        asset_name = data.get(
            "name",
            ASSET_NAMES.get(normalized_symbol, normalized_symbol),
        )

        if data.get("status") == "unavailable":
            return (
                f"{asset_name} ({normalized_symbol})\n\n"
                "This asset is not currently supported by Jarvis."
            )

        asset = data.get("asset")

        if not isinstance(asset, dict):
            return (
                f"{asset_name} ({normalized_symbol})\n\n"
                "No current market observation is available."
            )

        moves_data = data.get("moves", {})
        moves = (
            moves_data.get("moves", [])
            if isinstance(moves_data, dict)
            else []
        )

        latest_move = (
            moves[0]
            if moves and isinstance(moves[0], dict)
            else None
        )

        alerts = data.get("alerts", {})
        if not isinstance(alerts, dict):
            alerts = {}

        news = data.get("news", {})
        if not isinstance(news, dict):
            news = {}

        articles = [
            article
            for article in news.get("articles", [])
            if isinstance(article, dict)
        ]

        lines = [
            f"{asset_name} ({normalized_symbol})",
            "",
            f"Price: {self._format_price(asset.get('price_usd'))}",
            (
                "Provider change: "
                f"{self._format_percent(
                    asset.get('provider_change_percent')
                )}"
            ),
        ]

        if latest_move:
            direction = str(
                latest_move.get("direction", "unknown")
            ).lower()

            lines.append(
                (
                    "15-minute move: "
                    f"{self._format_percent(
                        latest_move.get(
                            'interval_change_percent'
                        )
                    )} "
                    f"({direction})"
                )
            )
        else:
            lines.append(
                "15-minute move: No comparison available"
            )

        lines.extend(
            [
                f"Data source: {asset.get('provider') or 'Unknown'}",
                (
                    "Last observed: "
                    f"{self._format_timestamp(
                        asset.get('observed_at')
                    )}"
                ),
                (
                    "Recent alerts: "
                    f"{alerts.get('returned', 0)}"
                ),
                (
                    "Linked news returned: "
                    f"{news.get('count', 0)}"
                ),
            ]
        )

        alert_items = [
            alert
            for alert in alerts.get("alerts", [])
            if isinstance(alert, dict)
        ]

        if alert_items:
            lines.extend(["", "Recent alerts:"])

            for alert in alert_items[:2]:
                severity = str(
                    alert.get("severity", "unknown")
                ).upper()
                message = alert.get(
                    "message",
                    "Alert details unavailable.",
                )

                lines.append(
                    f"- [{severity}] {message}"
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

                lines.append(
                    f"- {title} — {source}"
                )

        return "\n".join(lines)
