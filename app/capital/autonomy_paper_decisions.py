"""Deterministic lifecycle rules for paper experiments.

Inputs must come from the accounting reader and verified factory review,
never from model-generated performance claims.

Realized loss is not portfolio drawdown. Sell fills are not a count of
completed round trips. Unrealized risk remains subject to position exits.
"""

from decimal import Decimal, InvalidOperation


RULE_VERSION = "paper_realized_loss_and_duration_v1"
REALIZED_LOSS_LIMIT = Decimal("50")
MINIMUM_SELL_FILLS = 30
MAXIMUM_ACTIVE_SECONDS = 180 * 24 * 60 * 60
STATES = {"planned", "active", "paused", "demoted", "retired"}


def choose_paper_transition(
    *,
    status,
    accounting_valid,
    realized_gain_loss_usd,
    sell_fill_count,
    holding_count,
    active_age_seconds,
    activation_eligible,
):
    if status not in STATES:
        raise ValueError("Unsupported lifecycle state.")
    for value in (accounting_valid, activation_eligible):
        if type(value) is not bool:
            raise ValueError("Eligibility flags must be booleans.")
    for value in (sell_fill_count, holding_count, active_age_seconds):
        if type(value) is not int or value < 0:
            raise ValueError("Counts and elapsed seconds must be nonnegative integers.")

    def decision(target, reason):
        return {
            "rule_version": RULE_VERSION,
            "target": target,
            "reason": reason,
            "live_capital_authorized": False,
        }

    if status == "retired":
        return decision(None, "Experiment is retired.")
    if not accounting_valid:
        return decision(
            "paused" if status == "active" else None,
            "Accounting evidence is incomplete or inconsistent.",
        )

    if isinstance(realized_gain_loss_usd, bool):
        raise ValueError("Realized P/L must be finite.")
    try:
        realized = Decimal(str(realized_gain_loss_usd))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("Realized P/L must be finite.") from error
    if not realized.is_finite():
        raise ValueError("Realized P/L must be finite.")

    if status == "demoted":
        return decision(
            "retired" if holding_count == 0 else None,
            "Demoted experiment is empty."
            if holding_count == 0
            else "Awaiting existing-position exits before retirement.",
        )

    if status in {"active", "paused"}:
        if realized <= -REALIZED_LOSS_LIMIT:
            return decision("demoted", "Realized loss reached the $50 limit.")
        if sell_fill_count >= MINIMUM_SELL_FILLS and realized < 0:
            return decision(
                "demoted",
                "Cumulative realized P/L is negative after at least 30 sell fills.",
            )
        if active_age_seconds >= MAXIMUM_ACTIVE_SECONDS:
            return decision("demoted", "The 180-day experiment period ended.")

    if status == "planned" and activation_eligible:
        return decision("active", "Verified research and validation permit activation.")
    if status == "paused":
        return decision(None, "Paused experiment requires a separate resumption decision.")
    return decision(None, "No lifecycle transition is required.")
