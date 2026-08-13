from datetime import datetime, timezone
from typing import Any

from app.autonomous_trading.decision_queries import (
    get_recent_trade_decisions,
)
from app.autonomous_trading.journal_queries import (
    get_trade_journal,
)
from app.autonomous_trading.learning_loop import (
    get_learning_report,
)
from app.autonomous_trading.policy import (
    INITIAL_1000_POLICY,
)
from app.market_db.portfolio_queries import (
    get_portfolio_summary,
)


EXPERIMENT_DURATION_DAYS = 180

EXPERIMENT_STARTED_AT = datetime(
    2026,
    8,
    13,
    9,
    14,
    1,
    tzinfo=timezone.utc,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _experiment_day(
    *,
    now: datetime,
) -> int:
    elapsed = now - EXPERIMENT_STARTED_AT

    day_number = elapsed.days + 1

    if day_number < 1:
        return 1

    if day_number > EXPERIMENT_DURATION_DAYS:
        return EXPERIMENT_DURATION_DAYS

    return day_number


def get_experiment_status(
    *,
    portfolio_id: int | None = None,
    decision_limit: int = 10,
) -> dict[str, Any]:
    """
    Return the dashboard snapshot for the autonomous
    paper-trading experiment.
    """

    now = _utc_now()

    portfolio = get_portfolio_summary(
        portfolio_id=portfolio_id,
        transaction_limit=10,
    )

    learning = get_learning_report()

    decisions = get_recent_trade_decisions(
        limit=decision_limit,
    )

    open_journal = get_trade_journal(
        status="open",
        limit=100,
    )

    closed_journal = get_trade_journal(
        status="closed",
        limit=100,
    )

    starting_capital = float(
        INITIAL_1000_POLICY.starting_capital_usd
    )

    current_value = float(
        portfolio.get("total_value_usd")
        or 0
    )

    total_return_percent = 0.0

    if starting_capital > 0:
        total_return_percent = (
            (
                current_value
                - starting_capital
            )
            / starting_capital
            * 100
        )

    summary = learning["summary"]

    return {
        "status": "success",
        "generated_at": now.isoformat(),
        "experiment": {
            "name": (
                "Autonomous Paper Trading Experiment"
            ),
            "strategy_name": (
                "momentum_alignment_v1"
            ),
            "execution_mode": (
                "autonomous_paper_trading"
                if (
                    INITIAL_1000_POLICY
                    .autonomous_execution_enabled
                )
                else "disabled"
            ),
            "started_at": (
                EXPERIMENT_STARTED_AT.isoformat()
            ),
            "duration_days": (
                EXPERIMENT_DURATION_DAYS
            ),
            "day_number": _experiment_day(
                now=now,
            ),
            "starting_capital_usd": (
                starting_capital
            ),
        },
        "portfolio": {
            "current_value_usd": (
                current_value
            ),
            "cash_balance_usd": float(
                portfolio.get(
                    "cash_balance_usd"
                )
                or 0
            ),
            "market_value_usd": float(
                portfolio.get(
                    "market_value_usd"
                )
                or 0
            ),
            "realized_gain_loss_usd": float(
                portfolio.get(
                    "realized_gain_loss_usd"
                )
                or 0
            ),
            "unrealized_gain_loss_usd": float(
                portfolio.get(
                    "unrealized_gain_loss_usd"
                )
                or 0
            ),
            "total_gain_loss_usd": float(
                portfolio.get(
                    "total_gain_loss_usd"
                )
                or 0
            ),
            "total_return_percent": (
                total_return_percent
            ),
            "open_position_count": int(
                portfolio.get(
                    "position_count"
                )
                or 0
            ),
            "transaction_count": int(
                portfolio.get(
                    "transaction_count"
                )
                or 0
            ),
        },
        "performance": {
            "completed_trade_count": int(
                summary.get(
                    "closed_trade_count"
                )
                or 0
            ),
            "wins": int(
                summary.get("wins")
                or 0
            ),
            "losses": int(
                summary.get("losses")
                or 0
            ),
            "breakeven": int(
                summary.get("breakeven")
                or 0
            ),
            "win_rate_percent": (
                summary.get(
                    "win_rate_percent"
                )
            ),
            "average_return_percent": (
                summary.get(
                    "average_return_percent"
                )
            ),
            "thesis_accuracy_percent": (
                summary.get(
                    "thesis_accuracy_percent"
                )
            ),
        },
        "journal": {
            "open_count": int(
                open_journal["count"]
            ),
            "closed_count": int(
                closed_journal["count"]
            ),
        },
        "recent_decisions": (
            decisions
        ),
        "learning": learning,
    }
