from datetime import datetime, timedelta
from decimal import Decimal


MAX_HOLDING_TIME = timedelta(hours=24)


def frozen_recovery_target(
    *,
    entry_mean: Decimal,
    entry_std: Decimal,
) -> Decimal:
    if not entry_mean.is_finite() or entry_mean <= 0:
        raise ValueError("Entry mean must be finite and positive.")

    if not entry_std.is_finite() or entry_std <= 0:
        raise ValueError("Entry deviation must be finite and positive.")

    target = entry_mean - Decimal("0.25") * entry_std

    if target <= 0:
        raise ValueError("Recovery target must be positive.")

    return target


def evaluate_fixed_exit(
    *,
    current_price: Decimal,
    recovery_target: Decimal,
    opened_at: datetime,
    now: datetime,
) -> str | None:
    for value in (current_price, recovery_target):
        if not value.is_finite() or value <= 0:
            raise ValueError("Prices must be finite and positive.")

    if opened_at.utcoffset() is None or now.utcoffset() is None:
        raise ValueError("Timestamps must include a timezone.")

    if now < opened_at:
        raise ValueError("Evaluation time precedes entry.")

    if current_price >= recovery_target:
        return "fixed_mean_recovery"

    if now - opened_at >= MAX_HOLDING_TIME:
        return "recovery_timeout"

    return None
