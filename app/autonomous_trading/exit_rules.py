from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from app.autonomous_trading.policy import RiskPolicy
from app.autonomous_trading.strategy import PositionContext


class ExitRule(str, Enum):
    NONE = "none"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    MOMENTUM_REVERSAL = "momentum_reversal"
    MAX_HOLDING_EXPOSURE = "max_holding_exposure"
    MAX_POSITION_DURATION = "max_position_duration"


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    rule: ExitRule
    rationale: str


def evaluate_exit_rules(
    *,
    position_context: PositionContext,
    policy: RiskPolicy,
    momentum_reversal: bool = False,
) -> ExitDecision:
    if not position_context.has_position:
        return ExitDecision(
            should_exit=False,
            rule=ExitRule.NONE,
            rationale=(
                f"No open {position_context.symbol} "
                f"position exists."
            ),
        )

    gain_loss_percent = (
        position_context.unrealized_gain_loss_percent
    )

    stop_loss_trigger = -policy.stop_loss_percent

    if gain_loss_percent <= stop_loss_trigger:
        return ExitDecision(
            should_exit=True,
            rule=ExitRule.STOP_LOSS,
            rationale=(
                f"{position_context.symbol} triggered "
                f"the stop-loss rule. "
                f"Unrealized P/L is "
                f"{gain_loss_percent:.2f}% versus "
                f"the -{policy.stop_loss_percent:.2f}% "
                f"threshold."
            ),
        )

    if gain_loss_percent >= policy.take_profit_percent:
        return ExitDecision(
            should_exit=True,
            rule=ExitRule.TAKE_PROFIT,
            rationale=(
                f"{position_context.symbol} triggered "
                f"the take-profit rule. "
                f"Unrealized P/L is "
                f"{gain_loss_percent:.2f}% versus "
                f"the +{policy.take_profit_percent:.2f}% "
                f"threshold."
            ),
        )

    if momentum_reversal:
        return ExitDecision(
            should_exit=True,
            rule=ExitRule.MOMENTUM_REVERSAL,
            rationale=(
                f"{position_context.symbol} triggered "
                f"the momentum-reversal exit rule."
            ),
        )

    if (
        position_context.allocation_percent
        > policy.max_position_percent
    ):
        return ExitDecision(
            should_exit=True,
            rule=ExitRule.MAX_HOLDING_EXPOSURE,
            rationale=(
                f"{position_context.symbol} exceeded "
                f"the maximum holding exposure. "
                f"Current allocation is "
                f"{position_context.allocation_percent:.2f}% "
                f"versus the "
                f"{policy.max_position_percent:.2f}% limit."
            ),
        )

    if position_context.opened_at is not None:
        opened_at = position_context.opened_at

        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(
                tzinfo=timezone.utc
            )

        position_age = (
            datetime.now(timezone.utc)
            - opened_at
        )

        if position_age.days >= policy.max_position_duration_days:
            return ExitDecision(
                should_exit=True,
                rule=ExitRule.MAX_POSITION_DURATION,
                rationale=(
                    f"{position_context.symbol} exceeded "
                    f"the maximum position duration. "
                    f"Position age is "
                    f"{position_age.days} days versus "
                    f"the "
                    f"{policy.max_position_duration_days}-day "
                    f"limit."
                ),
            )

    return ExitDecision(
        should_exit=False,
        rule=ExitRule.NONE,
        rationale=(
            f"No exit threshold triggered. "
            f"Unrealized P/L is "
            f"{gain_loss_percent:.2f}%."
        ),
    )
