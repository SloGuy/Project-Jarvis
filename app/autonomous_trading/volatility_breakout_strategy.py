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
from app.market_db.history import get_market_history


STRATEGY_NAME = "volatility_breakout_v1"

LOOKBACK_OBSERVATIONS = 60
MINIMUM_OBSERVATIONS = 41
COMPRESSION_WINDOW = 20
BASELINE_WINDOW = 20
EXIT_AVERAGE_WINDOW = 10

MAX_COMPRESSION_RATIO = Decimal("0.60")
BREAKOUT_BUFFER_PERCENT = Decimal("0.25")
MINIMUM_EXPANSION_RATIO = Decimal("2.00")

DEFAULT_POSITION_PERCENT = Decimal("10.00")
BASE_CONFIDENCE_PERCENT = Decimal("70.00")
MAX_CONFIDENCE_PERCENT = Decimal("95.00")


@dataclass(frozen=True)
class VolatilityBreakoutSnapshot:
    symbol: str
    observation_at: datetime | None
    latest_price_usd: Decimal | None
    compressed_range_high_usd: Decimal | None
    compressed_range_low_usd: Decimal | None
    compression_ratio: Decimal | None
    breakout_percent: Decimal | None
    expansion_ratio: Decimal | None
    exit_average_usd: Decimal | None
    observation_count: int
    usable: bool
    reason: str | None


def _range_percent(
    prices: list[Decimal],
) -> Decimal:
    average = (
        sum(prices, Decimal("0"))
        / Decimal(len(prices))
    )

    if average <= 0:
        return Decimal("0")

    return (
        (max(prices) - min(prices))
        / average
        * Decimal("100")
    )


def get_volatility_breakout_snapshot(
    *,
    symbol: str,
) -> VolatilityBreakoutSnapshot:
    normalized_symbol = symbol.strip().upper()

    if not normalized_symbol:
        raise ValueError("symbol must not be empty.")

    history = get_market_history(
        symbol=normalized_symbol,
        limit=LOOKBACK_OBSERVATIONS,
    )

    observations = [
        observation
        for observation in (
            history.get("observations") or []
        )
        if (
            observation.get("price_usd") is not None
            and observation.get("observed_at") is not None
            and Decimal(
                str(observation["price_usd"])
            )
            > 0
        )
    ]

    if len(observations) < MINIMUM_OBSERVATIONS:
        return VolatilityBreakoutSnapshot(
            symbol=normalized_symbol,
            observation_at=None,
            latest_price_usd=None,
            compressed_range_high_usd=None,
            compressed_range_low_usd=None,
            compression_ratio=None,
            breakout_percent=None,
            expansion_ratio=None,
            exit_average_usd=None,
            observation_count=len(observations),
            usable=False,
            reason=(
                f"At least {MINIMUM_OBSERVATIONS} "
                "usable price observations are required."
            ),
        )

    prices = [
        Decimal(str(item["price_usd"]))
        for item in observations
    ]

    latest_price = prices[0]

    compression_prices = prices[
        1:1 + COMPRESSION_WINDOW
    ]

    baseline_start = 1 + COMPRESSION_WINDOW
    baseline_end = (
        baseline_start + BASELINE_WINDOW
    )

    baseline_prices = prices[
        baseline_start:baseline_end
    ]

    compression_range = _range_percent(
        compression_prices
    )
    baseline_range = _range_percent(
        baseline_prices
    )

    if baseline_range <= 0:
        return VolatilityBreakoutSnapshot(
            symbol=normalized_symbol,
            observation_at=None,
            latest_price_usd=latest_price,
            compressed_range_high_usd=max(
                compression_prices
            ),
            compressed_range_low_usd=min(
                compression_prices
            ),
            compression_ratio=None,
            breakout_percent=None,
            expansion_ratio=None,
            exit_average_usd=None,
            observation_count=len(prices),
            usable=False,
            reason="Baseline price range is zero.",
        )

    compressed_high = max(
        compression_prices
    )
    compressed_low = min(
        compression_prices
    )

    compression_ratio = (
        compression_range / baseline_range
    )

    breakout_percent = (
        (latest_price - compressed_high)
        / compressed_high
        * Decimal("100")
    )

    previous_price = prices[1]

    latest_move_percent = abs(
        (latest_price - previous_price)
        / previous_price
        * Decimal("100")
    )

    window_moves = [
        abs(
            (current - previous)
            / previous
            * Decimal("100")
        )
        for current, previous in zip(
            compression_prices,
            compression_prices[1:],
        )
        if previous > 0
    ]

    average_window_move = (
        sum(window_moves, Decimal("0"))
        / Decimal(len(window_moves))
    )

    expansion_ratio = (
        latest_move_percent
        / average_window_move
        if average_window_move > 0
        else None
    )

    exit_prices = prices[
        :EXIT_AVERAGE_WINDOW
    ]

    exit_average = (
        sum(exit_prices, Decimal("0"))
        / Decimal(len(exit_prices))
    )

    observation_at = datetime.fromisoformat(
        str(
            observations[0]["observed_at"]
        ).replace("Z", "+00:00")
    )

    return VolatilityBreakoutSnapshot(
        symbol=normalized_symbol,
        observation_at=observation_at,
        latest_price_usd=latest_price,
        compressed_range_high_usd=compressed_high,
        compressed_range_low_usd=compressed_low,
        compression_ratio=compression_ratio,
        breakout_percent=breakout_percent,
        expansion_ratio=expansion_ratio,
        exit_average_usd=exit_average,
        observation_count=len(prices),
        usable=True,
        reason=None,
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


def _entry_confidence(
    *,
    breakout_percent: Decimal,
    expansion_ratio: Decimal,
) -> Decimal:
    breakout_bonus = max(
        Decimal("0"),
        breakout_percent
        - BREAKOUT_BUFFER_PERCENT,
    ) * Decimal("10")

    expansion_bonus = max(
        Decimal("0"),
        expansion_ratio
        - MINIMUM_EXPANSION_RATIO,
    ) * Decimal("2")

    return min(
        BASE_CONFIDENCE_PERCENT
        + breakout_bonus
        + expansion_bonus,
        MAX_CONFIDENCE_PERCENT,
    )


def evaluate_volatility_breakout_strategy(
    *,
    symbol: str,
    position_context: PositionContext,
    snapshot: (
        VolatilityBreakoutSnapshot | None
    ) = None,
) -> StrategyCandidate:
    normalized_symbol = symbol.strip().upper()

    if not normalized_symbol:
        raise ValueError("symbol must not be empty.")

    if snapshot is None:
        snapshot = (
            get_volatility_breakout_snapshot(
                symbol=normalized_symbol,
            )
        )

    if snapshot.symbol != normalized_symbol:
        raise ValueError(
            "snapshot symbol does not match "
            "strategy symbol."
        )

    if (
        not snapshot.usable
        or snapshot.observation_at is None
        or snapshot.latest_price_usd is None
        or snapshot.exit_average_usd is None
    ):
        return _hold_candidate(
            symbol=normalized_symbol,
            confidence_percent=Decimal("0"),
            rationale=(
                snapshot.reason
                or "Volatility-breakout snapshot "
                "is unusable."
            ),
        )

    if (
        position_context.has_position
        and snapshot.latest_price_usd
        < snapshot.exit_average_usd
    ):
        confirmation = update_signal_confirmation(
            symbol=normalized_symbol,
            strategy_name=STRATEGY_NAME,
            action=StrategyAction.SELL,
            observation_at=snapshot.observation_at,
        )

        if not confirmation.confirmed:
            return _hold_candidate(
                symbol=normalized_symbol,
                rationale=(
                    "Breakout-failure exit is pending "
                    f"confirmation "
                    f"({confirmation.confirmation_count}/"
                    f"{confirmation.required_confirmations})."
                ),
            )

        return create_strategy_candidate(
            symbol=normalized_symbol,
            action=StrategyAction.SELL,
            confidence_percent=Decimal("85.00"),
            rationale=(
                "Price broke below the short exit "
                f"average. Latest: "
                f"${snapshot.latest_price_usd:.4f}; "
                f"exit average: "
                f"${snapshot.exit_average_usd:.4f}."
            ),
            suggested_position_percent=Decimal("0"),
            strategy_name=STRATEGY_NAME,
        )

    if position_context.has_position:
        update_signal_confirmation(
            symbol=normalized_symbol,
            strategy_name=STRATEGY_NAME,
            action=StrategyAction.HOLD,
            observation_at=snapshot.observation_at,
        )

        return _hold_candidate(
            symbol=normalized_symbol,
            rationale=(
                "Breakout position remains above "
                f"its exit average. Latest: "
                f"${snapshot.latest_price_usd:.4f}; "
                f"exit average: "
                f"${snapshot.exit_average_usd:.4f}."
            ),
        )

    if (
        snapshot.compression_ratio is None
        or snapshot.breakout_percent is None
        or snapshot.expansion_ratio is None
    ):
        return _hold_candidate(
            symbol=normalized_symbol,
            confidence_percent=Decimal("0"),
            rationale=(
                "Compression or expansion evidence "
                "is unavailable."
            ),
        )

    entry_signal = (
        snapshot.compression_ratio
        <= MAX_COMPRESSION_RATIO
        and snapshot.breakout_percent
        >= BREAKOUT_BUFFER_PERCENT
        and snapshot.expansion_ratio
        >= MINIMUM_EXPANSION_RATIO
    )

    if entry_signal:
        confirmation = update_signal_confirmation(
            symbol=normalized_symbol,
            strategy_name=STRATEGY_NAME,
            action=StrategyAction.BUY,
            observation_at=snapshot.observation_at,
        )

        if not confirmation.confirmed:
            return _hold_candidate(
                symbol=normalized_symbol,
                rationale=(
                    "Volatility breakout is pending "
                    f"confirmation "
                    f"({confirmation.confirmation_count}/"
                    f"{confirmation.required_confirmations})."
                ),
            )

        return create_strategy_candidate(
            symbol=normalized_symbol,
            action=StrategyAction.BUY,
            confidence_percent=_entry_confidence(
                breakout_percent=(
                    snapshot.breakout_percent
                ),
                expansion_ratio=(
                    snapshot.expansion_ratio
                ),
            ),
            rationale=(
                "Confirmed breakout from volatility "
                f"compression. Compression ratio: "
                f"{snapshot.compression_ratio:.3f}; "
                f"breakout: "
                f"{snapshot.breakout_percent:.3f}%; "
                f"expansion ratio: "
                f"{snapshot.expansion_ratio:.3f}."
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
        observation_at=snapshot.observation_at,
    )

    return _hold_candidate(
        symbol=normalized_symbol,
        rationale=(
            "No confirmed volatility breakout. "
            f"Compression ratio: "
            f"{snapshot.compression_ratio:.3f}; "
            f"breakout: "
            f"{snapshot.breakout_percent:.3f}%; "
            f"expansion ratio: "
            f"{snapshot.expansion_ratio:.3f}."
        ),
    )
