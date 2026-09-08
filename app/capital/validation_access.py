"""Reservation checks for shared historical snapshot access."""
from contextlib import contextmanager
from datetime import datetime, timezone

from app.capital.validation_plan import timestamp
from app.capital.validation_registry import locked_state


def now_utc():
    return datetime.now(timezone.utc)


def aware(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Historical access requires timezone-aware times.")
    return value.astimezone(timezone.utc)


@contextmanager
def historical_access(
    *, asset_id, provider, decision_at,
    validation_plan_id=None, run_token=None,
):
    decision = aware(decision_at)
    if (validation_plan_id is None) != (run_token is None):
        raise ValueError("Validation access requires both plan ID and run token.")

    # Keep the registry locked through the database read and row checks.
    with locked_state() as state:
        if decision > now_utc():
            raise ValueError("Historical decision time cannot be in the future.")
        own = None
        if validation_plan_id is not None:
            own = state["plans"].get(validation_plan_id)
            if (
                own is None
                or own["status"] != "running"
                or own.get("run_token") != run_token
            ):
                raise ValueError("Validation run ownership is not valid.")
            plan = own["envelope"]["plan"]
            if (
                plan["asset_id"] != asset_id
                or plan["provider"] != provider
                or not (
                    timestamp(plan["start"]) <= decision
                    < timestamp(plan["end_exclusive"])
                )
            ):
                raise ValueError("Historical request differs from validation plan.")

        protected = []
        for row in state["plans"].values():
            if row is own:
                continue
            plan = row["envelope"]["plan"]
            if plan["asset_id"] == asset_id and plan["provider"] == provider:
                protected.append((
                    timestamp(plan["start"]),
                    timestamp(plan["end_exclusive"]),
                ))

        def check_times(values):
            for value in values:
                observed = aware(value)
                if any(start <= observed < end for start, end in protected):
                    raise ValueError(
                        "Reserved validation period encountered in historical "
                        "access, including possible warm-up observations."
                    )

        check_times([decision])
        yield check_times
