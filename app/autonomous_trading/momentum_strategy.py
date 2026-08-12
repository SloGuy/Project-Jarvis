from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.autonomous_trading.signal_confirmation import (
    update_signal_confirmation,
)
from app.autonomous_trading.strategy import (
    PositionContext,
    StrategyAction,
    StrategyCandidate,
    create_strategy_candidate,
)
from app.market_db.moves import get_latest_market_moves
from app.market_db.trends import get_asset_trend


STRATEGY_NAME = "momentum_alignment_v1"

SHORT_TERM_MINIMUM_PERCENT = Decimal("0.25")
TREND_MINIMUM_PERCENT = Decimal("0.50")

DEFAULT_POSITION_PERCENT = Decimal("10.00")

BASE_CONFIDENCE_PERCENT = Decimal("70.00")
MAX_CONFIDENCE_PERCENT = Decimal("95.00")


@dataclass(frozen=True)
class MomentumSnapshot:
    symbol: str
    observation_at: datetime | None
    short_term_percent: Decimal | None
    trend_percent: Decimal | None
    usable: bool
    reason: str | None


def get_momentum_snapshot(
    *,
    symbol: str,
) -> MomentumSnapshot:
    normalized_symbol = symbol.strip().upper()

    if not normalized_symbol:
        raise ValueError(
            "symbol must not be empty."
        )

    moves = get_latest_market_moves(
        symbol=normalized_symbol,
        comparison_minutes=15,
        minimum_move_percent=0.0,
        limit=1,
    )

    trend = get_asset_trend(
        symbol=normalized_symbol,
        hours=24,
    )

    move_rows = moves.get("moves") or []

    if not move_rows:
        return MomentumSnapshot(
            symbol=normalized_symbol,
            observation_at=None,
            short_term_percent=None,
            trend_percent=None,
            usable=False,
            reason=(
                "No usable 15-minute move is available."
            ),
        )

    move = move_rows[0]

    observed_at_value = move.get(
        "latest_observed_at"
    )

    if observed_at_value is None:
        return MomentumSnapshot(
            symbol=normalized_symbol,
            observation_at=None,
            short_term_percent=None,
            trend_percent=None,
            usable=False,
            reason=(
                "Latest market observation timestamp "
                "is unavailable."
            ),
        )

    observation_at = datetime.fromisoformat(
        str(observed_at_value).replace(
            "Z",
            "+00:00",
        )
    )

    if trend.get("status") != "healthy":
        return MomentumSnapshot(
            symbol=normalized_symbol,
            observation_at=observation_at,
            short_term_percent=None,
            trend_percent=None,
            usable=False,
            reason=(
                "24-hour trend data is unavailable."
            ),
        )

    if not trend.get("window_fully_covered"):
        return MomentumSnapshot(
            symbol=normalized_symbol,
            observation_at=observation_at,
            short_term_percent=None,
            trend_percent=None,
            usable=False,
            reason=(
                "The requested 24-hour trend window "
                "is not fully covered."
            ),
        )

    short_term_value = move.get(
        "interval_change_percent"
    )

    statistics = trend.get("statistics") or {}

    trend_value = statistics.get(
        "change_percent"
    )

    if (
        short_term_value is None
        or trend_value is None
    ):
        return MomentumSnapshot(
            symbol=normalized_symbol,
            observation_at=observation_at,
            short_term_percent=None,
            trend_percent=None,
            usable=False,
            reason=(
                "Required momentum measurements "
                "are unavailable."
            ),
        )

    return MomentumSnapshot(
        symbol=normalized_symbol,
        observation_at=observation_at,
        short_term_percent=Decimal(
            str(short_term_value)
        ),
        trend_percent=Decimal(
            str(trend_value)
        ),
        usable=True,
        reason=None,
    )


def _confidence_from_signals(
    *,
    short_term_percent: Decimal,
    trend_percent: Decimal,
) -> Decimal:
    strength = (
        abs(short_term_percent)
        + abs(trend_percent)
    )

    confidence = (
        BASE_CONFIDENCE_PERCENT
        + strength
    )

    return min(
        confidence,
        MAX_CONFIDENCE_PERCENT,
    )


def has_negative_momentum_reversal(
    *,
    short_term_percent: Decimal,
    trend_percent: Decimal,
) -> bool:
    return (
        short_term_percent
        <= -SHORT_TERM_MINIMUM_PERCENT
        and trend_percent
        <= -TREND_MINIMUM_PERCENT
    )


def _hold_candidate(
    *,
    symbol: str,
    rationale: str,
    confidence_percent: Decimal = Decimal("50.00"),
) -> StrategyCandidate:
    return create_strategy_candidate(
        symbol=symbol,
        action=StrategyAction.HOLD,
        confidence_percent=confidence_percent,
        rationale=rationale,
        suggested_position_percent=Decimal("0"),
        strategy_name=STRATEGY_NAME,
    )


def evaluate_momentum_strategy(
    *,
    symbol: str,
    position_context: PositionContext,
    snapshot: MomentumSnapshot | None = None,
) -> StrategyCandidate:
    normalized_symbol = symbol.strip().upper()

    if not normalized_symbol:
        raise ValueError(
            "symbol must not be empty."
        )

    if snapshot is None:
        snapshot = get_momentum_snapshot(
            symbol=normalized_symbol,
        )

    if snapshot.symbol != normalized_symbol:
        raise ValueError(
            "snapshot symbol does not match strategy symbol."
        )

    if not snapshot.usable:
        if snapshot.observation_at is not None:
            update_signal_confirmation(
                symbol=normalized_symbol,
                strategy_name=STRATEGY_NAME,
                action=StrategyAction.HOLD,
                observation_at=snapshot.observation_at,
            )

        return _hold_candidate(
            symbol=normalized_symbol,
            confidence_percent=Decimal("0"),
            rationale=(
                snapshot.reason
                or "Momentum snapshot is unusable."
            ),
        )

    if (
        snapshot.observation_at is None
        or snapshot.short_term_percent is None
        or snapshot.trend_percent is None
    ):
        return _hold_candidate(
            symbol=normalized_symbol,
            confidence_percent=Decimal("0"),
            rationale=(
                "Momentum snapshot is incomplete."
            ),
        )

    latest_observed_at = snapshot.observation_at
    short_term_percent = snapshot.short_term_percent
    trend_percent = snapshot.trend_percent

    buy_signal = (
        short_term_percent
        >= SHORT_TERM_MINIMUM_PERCENT
        and trend_percent
        >= TREND_MINIMUM_PERCENT
    )

    sell_signal = (
        short_term_percent
        <= -SHORT_TERM_MINIMUM_PERCENT
        and trend_percent
        <= -TREND_MINIMUM_PERCENT
    )

    if buy_signal and position_context.has_position:
        update_signal_confirmation(
            symbol=normalized_symbol,
            strategy_name=STRATEGY_NAME,
            action=StrategyAction.HOLD,
            observation_at=latest_observed_at,
        )

        return _hold_candidate(
            symbol=normalized_symbol,
            rationale=(
                f"Positive momentum detected, but an existing "
                f"position is already open. "
                f"Quantity: {position_context.quantity}; "
                f"allocation: "
                f"{position_context.allocation_percent:.2f}%; "
                f"unrealized P/L: "
                f"{position_context.unrealized_gain_loss_percent:.2f}%."
            ),
        )

    if sell_signal and not position_context.has_position:
        update_signal_confirmation(
            symbol=normalized_symbol,
            strategy_name=STRATEGY_NAME,
            action=StrategyAction.HOLD,
            observation_at=latest_observed_at,
        )

        return _hold_candidate(
            symbol=normalized_symbol,
            rationale=(
                f"Negative momentum detected, but there is "
                f"no open {normalized_symbol} position to sell."
            ),
        )

    if buy_signal:
        confirmation = update_signal_confirmation(
            symbol=normalized_symbol,
            strategy_name=STRATEGY_NAME,
            action=StrategyAction.BUY,
            observation_at=latest_observed_at,
        )

        if not confirmation.confirmed:
            return _hold_candidate(
                symbol=normalized_symbol,
                rationale=(
                    f"BUY signal is pending confirmation "
                    f"({confirmation.confirmation_count}/"
                    f"{confirmation.required_confirmations}). "
                    f"15-minute move: "
                    f"{short_term_percent:.3f}%; "
                    f"24-hour trend: "
                    f"{trend_percent:.3f}%."
                ),
            )

        confidence = _confidence_from_signals(
            short_term_percent=short_term_percent,
            trend_percent=trend_percent,
        )

        return create_strategy_candidate(
            symbol=normalized_symbol,
            action=StrategyAction.BUY,
            confidence_percent=confidence,
            rationale=(
                f"Confirmed positive momentum after "
                f"{confirmation.confirmation_count} "
                f"distinct market observations. "
                f"15-minute move "
                f"{short_term_percent:.3f}% and "
                f"24-hour trend {trend_percent:.3f}%."
            ),
            suggested_position_percent=(
                DEFAULT_POSITION_PERCENT
            ),
            strategy_name=STRATEGY_NAME,
        )

    if sell_signal:
        confirmation = update_signal_confirmation(
            symbol=normalized_symbol,
            strategy_name=STRATEGY_NAME,
            action=StrategyAction.SELL,
            observation_at=latest_observed_at,
        )

        if not confirmation.confirmed:
            return _hold_candidate(
                symbol=normalized_symbol,
                rationale=(
                    f"SELL signal is pending confirmation "
                    f"({confirmation.confirmation_count}/"
                    f"{confirmation.required_confirmations}). "
                    f"15-minute move: "
                    f"{short_term_percent:.3f}%; "
                    f"24-hour trend: "
                    f"{trend_percent:.3f}%."
                ),
            )

        confidence = _confidence_from_signals(
            short_term_percent=short_term_percent,
            trend_percent=trend_percent,
        )

        return create_strategy_candidate(
            symbol=normalized_symbol,
            action=StrategyAction.SELL,
            confidence_percent=confidence,
            rationale=(
                f"Confirmed negative momentum after "
                f"{confirmation.confirmation_count} "
                f"distinct market observations. "
                f"15-minute move "
                f"{short_term_percent:.3f}% and "
                f"24-hour trend {trend_percent:.3f}%."
            ),
            suggested_position_percent=(
                DEFAULT_POSITION_PERCENT
            ),
            strategy_name=STRATEGY_NAME,
        )

    update_signal_confirmation(
        symbol=normalized_symbol,
        strategy_name=STRATEGY_NAME,
        action=StrategyAction.HOLD,
        observation_at=latest_observed_at,
    )

    if position_context.has_position:
        return _hold_candidate(
            symbol=normalized_symbol,
            rationale=(
                f"No aligned momentum exit signal for the "
                f"existing position. "
                f"Quantity: {position_context.quantity}; "
                f"average cost: "
                f"${position_context.average_cost_usd:.2f}; "
                f"market value: "
                f"${position_context.market_value_usd:.2f}; "
                f"allocation: "
                f"{position_context.allocation_percent:.2f}%; "
                f"unrealized P/L: "
                f"{position_context.unrealized_gain_loss_percent:.2f}%. "
                f"15-minute move: "
                f"{short_term_percent:.3f}%; "
                f"24-hour trend: "
                f"{trend_percent:.3f}%."
            ),
        )

    return _hold_candidate(
        symbol=normalized_symbol,
        rationale=(
            f"No aligned momentum entry signal. "
            f"15-minute move: {short_term_percent:.3f}%; "
            f"24-hour trend: {trend_percent:.3f}%."
        ),
    )
