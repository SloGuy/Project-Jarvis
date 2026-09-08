"""Snapshot arithmetic for preselected, newest-first observations.
The caller owns time cutoffs, provider selection and window limits.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

LOOKBACK_OBSERVATIONS = 48
MINIMUM_OBSERVATIONS = 20

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


def calculate_mean_reversion_snapshot(
    *,
    symbol: str,
    observations: list[dict],
) -> MeanReversionSnapshot:
    normalized_symbol = symbol.strip().upper()

    if not normalized_symbol:
        raise ValueError("symbol must not be empty.")


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

