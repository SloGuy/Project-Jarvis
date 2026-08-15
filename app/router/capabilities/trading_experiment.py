from typing import Any

from app.autonomous_trading.journal_analytics import (
    get_journal_analytics,
    summarize_journal_analytics,
)
from app.router.capabilities.base import RouterCapability


TRADING_EXPERIMENT_PATTERNS = (
    r"\bpaper\s+trading\s+experiment\b",
    r"\bpaper\s+trading\s+performance\b",
    r"\bautonomous\s+trading\s+performance\b",
    r"\bautonomous\s+trading\s+experiment\b",
    r"\bhow\s+is\s+(the\s+)?paper\s+trading\s+(experiment\s+)?doing\b",
    r"\bhow\s+is\s+(the\s+)?autonomous\s+trading\s+(experiment\s+)?doing\b",
    r"\bhow\s+has\s+autonomous\s+trading\s+performed\b",
    r"\btrading\s+win\s+rate\b",
    r"\bcurrent\s+win\s+rate\b",
    r"\btrade\s+analytics\b",
    r"\btrading\s+analytics\b",
    r"\bwhat\s+is\s+jarvis\s+learning\s+from\s+trades\b",
    r"\bwhat\s+has\s+jarvis\s+learned\s+from\s+trades\b",
    r"\btrade\s+learning\b",
    r"\btrading\s+results\b",
)


class TradingExperimentCapability(
    RouterCapability
):
    name = "trading_experiment"
    endpoint = (
        "/market/autonomous/"
        "journal-analytics"
    )
    patterns = (
        TRADING_EXPERIMENT_PATTERNS
    )

    def execute(
        self,
        limit: int = 1000,
        **parameters: Any,
    ) -> dict[str, Any]:
        analytics = get_journal_analytics(
            limit=limit,
        )

        return {
            **analytics,
            "summary": (
                summarize_journal_analytics(
                    analytics
                )
            ),
        }

    def format_response(
        self,
        data: Any,
        **parameters: Any,
    ) -> str:
        if not isinstance(data, dict):
            raise TypeError(
                "Trading experiment capability "
                "expected dictionary data."
            )

        if data.get("status") != "success":
            return (
                "Autonomous trading analytics "
                "are currently unavailable."
            )

        summary = data.get("summary")

        if summary:
            return str(summary)

        return (
            summarize_journal_analytics(
                data
            )
        )
