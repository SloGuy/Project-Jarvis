from typing import Any

from app.router.capabilities.base import RouterCapability
from app.watchlists import get_watchlists


WATCHLIST_PATTERNS = (
    r"\bwatchlist\b",
    r"\bwatch list\b",
    r"\btracked\s+assets\b",
    r"\btracking\b",
    r"\bwhat\s+am\s+i\s+tracking\b",
    r"\bshow\s+my\s+watchlist\b",
    r"\blist\s+(?:my|the)\s+watchlist\b",
)


class WatchlistCapability(RouterCapability):
    name = "watchlist"
    endpoint = "/market/watchlist"
    patterns = WATCHLIST_PATTERNS

    def match(
        self,
        message: str,
    ) -> dict[str, Any] | None:
        result = super().match(
            message
        )

        if result is None:
            return None

        lowered = message.lower()

        if "crypto" in lowered:
            return {
                "asset_type": "crypto",
            }

        if (
            "stock" in lowered
            or "stocks" in lowered
        ):
            return {
                "asset_type": "stocks",
            }

        return {}

    def execute(
        self,
        asset_type: str | None = None,
        **parameters: Any,
    ) -> dict[str, Any]:
        watchlists = get_watchlists()

        stocks = watchlists.get(
            "stocks",
            [],
        )

        crypto = watchlists.get(
            "crypto",
            [],
        )

        if asset_type == "stocks":
            stocks_to_return = stocks
            crypto_to_return = []

        elif asset_type == "crypto":
            stocks_to_return = []
            crypto_to_return = crypto

        else:
            stocks_to_return = stocks
            crypto_to_return = crypto

        return {
            "asset_type": asset_type,
            "stocks": stocks_to_return,
            "crypto": crypto_to_return,
            "stock_count": len(
                stocks_to_return
            ),
            "crypto_count": len(
                crypto_to_return
            ),
            "total_assets": (
                len(stocks_to_return)
                + len(crypto_to_return)
            ),
            "updated_at": watchlists.get(
                "updated_at"
            ),
        }

    def format_response(
        self,
        data: Any,
        **parameters: Any,
    ) -> str:
        if not isinstance(
            data,
            dict,
        ):
            raise TypeError(
                "Watchlist capability expected dictionary data."
            )

        stocks = data.get(
            "stocks",
            [],
        )

        crypto = data.get(
            "crypto",
            [],
        )

        asset_type = data.get(
            "asset_type"
        )

        if asset_type == "stocks":
            title = (
                "Current Stock Watchlist"
            )

        elif asset_type == "crypto":
            title = (
                "Current Crypto Watchlist"
            )

        else:
            title = (
                "Current Jarvis Watchlist"
            )

        lines = [
            title,
            "",
            (
                "Total watchlist assets: "
                f"{data.get('total_assets', 0)}"
            ),
            "",
            "Stocks:",
        ]

        if stocks:
            for symbol in stocks:
                lines.append(
                    f"• {symbol}"
                )
        else:
            lines.append(
                "• None"
            )

        lines.extend(
            [
                "",
                "Crypto:",
            ]
        )

        if crypto:
            for symbol in crypto:
                lines.append(
                    f"• {symbol}"
                )
        else:
            lines.append(
                "• None"
            )

        return "\n".join(
            lines
        )
