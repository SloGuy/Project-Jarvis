from typing import Any

from app.market_db.portfolio_queries import get_portfolio_summary
from app.router.capabilities.base import RouterCapability


PORTFOLIO_PATTERNS = (
    r"\bportfolio\b",
    r"\bholdings?\b",
    r"\bpositions?\b",
    r"\bhow\s+am\s+i\s+doing\b",
    r"\bportfolio\s+value\b",
    r"\bportfolio\s+summary\b",
    r"\bmy\s+investments?\b",
    r"\ballocation\b",
)


class PortfolioCapability(RouterCapability):
    name = "portfolio"
    endpoint = "/market/portfolio"
    patterns = PORTFOLIO_PATTERNS

    def execute(
        self,
        portfolio_id: int | None = None,
        **parameters: Any,
    ) -> dict[str, Any]:
        return get_portfolio_summary(
            portfolio_id=portfolio_id,
        )

    def _format_currency(self, value: Any) -> str:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return "Unavailable"

        return f"${numeric_value:,.2f}"

    def _format_percent(self, value: Any) -> str:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return "Unavailable"

        return f"{numeric_value:+.2f}%"

    def _format_quantity(self, value: Any) -> str:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return "Unavailable"

        if numeric_value.is_integer():
            return f"{numeric_value:,.0f}"

        return f"{numeric_value:,.8f}".rstrip("0").rstrip(".")

    def format_response(
        self,
        data: Any,
        **parameters: Any,
    ) -> str:
        if not isinstance(data, dict):
            raise TypeError(
                "Portfolio capability expected dictionary data."
            )

        if data.get("status") == "not_found":
            return "Portfolio was not found."

        portfolio = data.get("portfolio", {})
        positions = data.get("positions", [])

        if not isinstance(portfolio, dict):
            portfolio = {}

        if not isinstance(positions, list):
            positions = []

        lines = [
            portfolio.get("name", "Portfolio"),
            "",
            (
                "Total value: "
                f"{self._format_currency(
                    data.get('total_value_usd')
                )}"
            ),
            (
                "Cash balance: "
                f"{self._format_currency(
                    data.get('cash_balance_usd')
                )}"
            ),
            (
                "Invested market value: "
                f"{self._format_currency(
                    data.get('market_value_usd')
                )}"
            ),
            (
                "Unrealized gain/loss: "
                f"{self._format_currency(
                    data.get('unrealized_gain_loss_usd')
                )}"
            ),
            (
                "Cash allocation: "
                f"{self._format_percent(
                    data.get('cash_allocation_percent')
                )}"
            ),
            (
                "Invested allocation: "
                f"{self._format_percent(
                    data.get('invested_allocation_percent')
                )}"
            ),
            f"Open positions: {data.get('position_count', 0)}",
        ]

        valid_positions = [
            position
            for position in positions
            if isinstance(position, dict)
        ]

        if not valid_positions:
            lines.extend(
                [
                    "",
                    "No open positions are currently held.",
                ]
            )

            return "\n".join(lines)

        lines.extend(["", "Holdings:"])

        for position in valid_positions:
            symbol = position.get("symbol", "Unknown")
            quantity = self._format_quantity(
                position.get("quantity")
            )
            market_value = self._format_currency(
                position.get("market_value_usd")
            )
            gain_loss = self._format_currency(
                position.get(
                    "unrealized_gain_loss_usd"
                )
            )
            gain_loss_percent = self._format_percent(
                position.get(
                    "unrealized_gain_loss_percent"
                )
            )
            allocation = self._format_percent(
                position.get("allocation_percent")
            )

            lines.append(
                (
                    f"• {symbol}: {quantity} units | "
                    f"Value {market_value} | "
                    f"P/L {gain_loss} "
                    f"({gain_loss_percent}) | "
                    f"Allocation {allocation}"
                )
            )

        return "\n".join(lines)
