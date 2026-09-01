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


STRATEGY_NAME = "mean_reversion_v1"

LOOKBACK_OBSERVATIONS = 48
MINIMUM_OBSERVATIONS = 20

ENTRY_Z_SCORE = Decimal("-1.50")
RECOVERY_Z_SCORE = Decimal("-0.25")

DEFAULT_POSITION_PERCENT = Decimal("10.00")
BASE_CONFIDENCE_PERCENT = Decimal("70.00")
MAX_CONFIDENCE_PERCENT = Decimal("95.00")


@dataclass(frozen=True)
class MeanReversionSnapshot:
    symbol: str
    observation_at: datetime | None
    latest_price_usd: Decimal | None
    mean_price_usd: Decimal | None
    standard_deviation_usd: Decimal | None
    z_score: Decimal | None
    observation_count: int
    usable: bool
    reason: str | None


def get_mean_reversion_snapshot(
    *,
    symbol: str,
) -> MeanReversionSnapshot:
    normalized_symbol = symbol.strip().upper()

    if not normalized_symbol:
        raise ValueError("symbol must not be empty.")

    history = get_market_history(
        symbol=normalized_symbol,
        limit=LOOKBACK_OBSERVATIONS,
    )

    observations = history.get("observations") or []

    usable_observations = [
        observation
        for observation in observations
        if (
            observation.get("price_usd") is not None
            and observation.get("observed_at") is not None
            and Decimal(str(observation["price_usd"]))
            > Decimal("0")
        )
    ]

    if len(usable_observations) < MINIMUM_OBSERVATIONS:
        return MeanReversionSnapshot(
            symbol=normalized_symbol,
            observation_at=None,
            latest_price_usd=None,
            mean_price_usd=None,
            standard_deviation_usd=None,
            z_score=None,
            observation_count=len(usable_observations),
            usable=False,
            reason=(
                f"At least {MINIMUM_OBSERVATIONS} usable price "
                f"observations are required."
            ),
        )

    prices = [
        Decimal(str(observation["price_usd"]))
        for observation in usable_observations
    ]

    observation_count = len(prices)
    count = Decimal(observation_count)

    mean_price = sum(
        prices,
        Decimal("0"),
    ) / count

    variance = sum(
        (
            (price - mean_price)
            * (price - mean_price)
        )
        for price in prices
    ) / count

    standard_deviation = variance.sqrt()

    latest_observation = usable_observations[0]
    latest_price = prices[0]

    observation_at = datetime.fromisoformat(
        str(latest_observation["observed_at"]).replace(
            "Z",
            "+00:00",
        )
    )

    if standard_deviation == Decimal("0"):
        return MeanReversionSnapshot(
            symbol=normalized_symbol,
            observation_at=observation_at,
            latest_price_usd=latest_price,
            mean_price_usd=mean_price,
            standard_deviation_usd=standard_deviation,
            z_score=None,
            observation_count=observation_count,
            usable=False,
            reason="Price variance is zero.",
        )

    z_score = (
        latest_price - mean_price
    ) / standard_deviation

    return MeanReversionSnapshot(
        symbol=normalized_symbol,
        observation_at=observation_at,
        latest_price_usd=latest_price,
        mean_price_usd=mean_price,
        standard_deviation_usd=standard_deviation,
        z_score=z_score,
        observation_count=observation_count,
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
    z_score: Decimal,
) -> Decimal:
    excess_deviation = max(
        Decimal("0"),
        abs(z_score) - abs(ENTRY_Z_SCORE),
    )

    return min(
        BASE_CONFIDENCE_PERCENT
        + excess_deviation * Decimal("10"),
        MAX_CONFIDENCE_PERCENT,
    )


def evaluate_mean_reversion_strategy(
    *,
    symbol: str,
    position_context: PositionContext,
    snapshot: MeanReversionSnapshot | None = None,
) -> StrategyCandidate:
    normalized_symbol = symbol.strip().upper()

    if not normalized_symbol:
        raise ValueError("symbol must not be empty.")

    if snapshot is None:
        snapshot = get_mean_reversion_snapshot(
            symbol=normalized_symbol,
        )

    if snapshot.symbol != normalized_symbol:
        raise ValueError(
            "snapshot symbol does not match strategy symbol."
        )

    if (
        not snapshot.usable
        or snapshot.observation_at is None
        or snapshot.latest_price_usd is None
        or snapshot.mean_price_usd is None
        or snapshot.z_score is None
    ):
        return _hold_candidate(
            symbol=normalized_symbol,
            confidence_percent=Decimal("0"),
            rationale=(
                snapshot.reason
                or "Mean-reversion snapshot is unusable."
            ),
        )

    if (
        position_context.has_position
        and snapshot.z_score >= RECOVERY_Z_SCORE
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
                    f"Mean recovery is pending confirmation "
                    f"({confirmation.confirmation_count}/"
                    f"{confirmation.required_confirmations}). "
                    f"Current z-score: {snapshot.z_score:.3f}."
                ),
            )

        return create_strategy_candidate(
            symbol=normalized_symbol,
            action=StrategyAction.SELL,
            confidence_percent=Decimal("85.00"),
            rationale=(
                f"Price recovered toward its rolling mean. "
                f"Current z-score: {snapshot.z_score:.3f}; "
                f"latest price: ${snapshot.latest_price_usd:.4f}; "
                f"rolling mean: ${snapshot.mean_price_usd:.4f}."
            ),
            suggested_position_percent=Decimal("0"),
            strategy_name=STRATEGY_NAME,
        )

    if (
        not position_context.has_position
        and snapshot.z_score <= ENTRY_Z_SCORE
    ):
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
                    f"Mean-reversion entry is pending confirmation "
                    f"({confirmation.confirmation_count}/"
                    f"{confirmation.required_confirmations}). "
                    f"Current z-score: {snapshot.z_score:.3f}."
                ),
            )

        return create_strategy_candidate(
            symbol=normalized_symbol,
            action=StrategyAction.BUY,
            confidence_percent=_entry_confidence(
                z_score=snapshot.z_score,
            ),
            rationale=(
                f"Price is unusually below its rolling mean. "
                f"Current z-score: {snapshot.z_score:.3f}; "
                f"latest price: ${snapshot.latest_price_usd:.4f}; "
                f"rolling mean: ${snapshot.mean_price_usd:.4f}; "
                f"observations: {snapshot.observation_count}."
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

    if position_context.has_position:
        return _hold_candidate(
            symbol=normalized_symbol,
            rationale=(
                f"Position remains below its mean-recovery exit. "
                f"Current z-score: {snapshot.z_score:.3f}; "
                f"recovery threshold: {RECOVERY_Z_SCORE}."
            ),
        )

    return _hold_candidate(
        symbol=normalized_symbol,
        rationale=(
            f"No mean-reversion entry signal. "
            f"Current z-score: {snapshot.z_score:.3f}; "
            f"entry threshold: {ENTRY_Z_SCORE}."
        ),
    )
