"""Select historical diagnostic prices without future-dated observations.

Observation timestamps do not prove when data became available.
The caller supplies the provider and permitted quote age explicitly.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from app.capital.portfolio_daily_returns import utc_timestamp


def _positive_id(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def _positive_price(value):
    if isinstance(value, bool):
        return None
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not price.is_finite() or price <= 0:
        return None
    return price


def select_historical_quote(
    *,
    observations,
    asset_id,
    provider,
    measured_at,
    maximum_age,
):
    """Select the latest matching observation at or before measurement.

    Each observation supplies id, asset_id, provider, observed_at,
    and price_usd. An invalid latest quote is not replaced by an
    older valid quote.
    """
    asset_id = _positive_id(asset_id, "asset_id")
    measured = utc_timestamp(measured_at)

    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("An explicit provider is required.")
    provider = provider.strip()

    if not isinstance(maximum_age, timedelta):
        raise ValueError("maximum_age must be a timedelta.")
    if maximum_age <= timedelta(0):
        raise ValueError("maximum_age must be positive.")

    candidates = []
    identifiers = set()
    timestamps = set()

    for row in observations:
        if row["asset_id"] != asset_id or row["provider"] != provider:
            continue

        observed = utc_timestamp(row["observed_at"])
        if observed > measured:
            continue

        observation_id = _positive_id(row["id"], "observation id")
        if observation_id in identifiers or observed in timestamps:
            raise ValueError("Duplicate matching price observations.")
        identifiers.add(observation_id)
        timestamps.add(observed)
        candidates.append((observed, observation_id, row))

    result = {
        "asset_id": asset_id,
        "provider": provider,
        "measured_at": measured.isoformat(),
        "maximum_age_seconds": maximum_age.total_seconds(),
        "status": "missing",
        "observation_id": None,
        "observed_at": None,
        "age_seconds": None,
        "price_usd": None,
        "historical_availability_verified": False,
    }

    if not candidates:
        return result

    observed, observation_id, row = max(
        candidates,
        key=lambda item: (item[0], item[1]),
    )
    age = measured - observed
    price = _positive_price(row["price_usd"])

    result.update(
        observation_id=observation_id,
        observed_at=observed.isoformat(),
        age_seconds=age.total_seconds(),
    )

    if price is None:
        result["status"] = "invalid"
    elif age > maximum_age:
        result["status"] = "stale"
        result["price_usd"] = str(price)
    else:
        result["status"] = "usable"
        result["price_usd"] = str(price)

    return result
