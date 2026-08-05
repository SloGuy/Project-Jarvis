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
    r"\btransactions?\b",
    r"\btrade\s+history\b",
    r"\brealized\s+(?:p/?l|profit|loss)\b",
    r"\bunrealized\s+(?:p/?l|profit|loss)\b",
)


class PortfolioCapability(RouterCapability):
    name = "portfolio"
    endpoint = "/market/portfolio"
    patterns = PORTFOLIO_PATTERNS

    def execute(
        self,
        portfolio_id: int | None = None,
        transaction_limit: int = 10,
        **parameters: Any,
    ) -> dict[str, Any]:
        return get_portfolio_summary(
            portfolio_id=portfolio_id,
            transaction_limit=transaction_limit,
        )

    def _format_currency(self, value: Any) -> str:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return "Unavailable"

        return f"${numeric_value:,.2f}"

    def _format_signed_currency(self, value: Any) -> str:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return "Unavailable"

        sign = "+" if numeric_value >= 0 else "-"
        return f"{sign}${abs(numeric_value):,.2f}"

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

    def _format_transaction(
        self,
        transaction: dict[str, Any],
    ) -> str:
        transaction_type = str(
            transaction.get("transaction_type", "unknown")
        ).lower()

        symbol = transaction.get("symbol")
        quantity = self._format_quantity(
            transaction.get("quantity")
        )
        total = self._format_currency(
            transaction.get("total_usd")
        )
        realized_gain_loss = transaction.get(
            "realized_gain_loss_usd"
        )

        if transaction_type == "deposit":
            return f"• Deposit: {total}"

        if transaction_type == "withdrawal":
            return f"• Withdrawal: {total}"

        if transaction_type == "buy":
            return (
                f"• Buy {quantity} {symbol or 'Unknown'} "
                f"for {total}"
            )

        if transaction_type == "sell":
            realized_text = self._format_signed_currency(
                realized_gain_loss
            )

            return (
                f"• Sell {quantity} {symbol or 'Unknown'} "
                f"for {total} | Realized P/L {realized_text}"
            )

        return (
            f"• {transaction_type.title()}: "
            f"{quantity} {symbol or ''} | {total}"
        ).rstrip()

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
        recent_transactions = data.get(
            "recent_transactions",
            [],
        )

        if not isinstance(portfolio, dict):
            portfolio = {}

        if not isinstance(positions, list):
            positions = []

        if not isinstance(recent_transactions, list):
            recent_transactions = []

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
                "Realized gain/loss: "
                f"{self._format_signed_currency(
                    data.get('realized_gain_loss_usd')
                )}"
            ),
            (
                "Unrealized gain/loss: "
                f"{self._format_signed_currency(
                    data.get('unrealized_gain_loss_usd')
                )}"
            ),
            (
                "Total gain/loss: "
                f"{self._format_signed_currency(
                    data.get('total_gain_loss_usd')
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
            f"Transactions: {data.get('transaction_count', 0)}",
        ]

        valid_positions = [
            position
            for position in positions
            if isinstance(position, dict)
        ]

        if valid_positions:
            lines.extend(["", "Holdings:"])

            for position in valid_positions:
                symbol = position.get("symbol", "Unknown")
                quantity = self._format_quantity(
                    position.get("quantity")
                )
                average_cost = self._format_currency(
                    position.get("average_cost_usd")
                )
                latest_price = self._format_currency(
                    position.get("latest_price_usd")
                )
                market_value = self._format_currency(
                    position.get("market_value_usd")
                )
                gain_loss = self._format_signed_currency(
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
                        f"Avg cost {average_cost} | "
                        f"Price {latest_price} | "
                        f"Value {market_value} | "
                        f"P/L {gain_loss} "
                        f"({gain_loss_percent}) | "
                        f"Allocation {allocation}"
                    )
                )
        else:
            lines.extend(
                [
                    "",
                    "No open positions are currently held.",
                ]
            )

        valid_transactions = [
            transaction
            for transaction in recent_transactions
            if isinstance(transaction, dict)
        ]

        if valid_transactions:
            lines.extend(["", "Recent transactions:"])

            for transaction in valid_transactions:
                lines.append(
                    self._format_transaction(transaction)
                )

        return "\n".join(lines)
