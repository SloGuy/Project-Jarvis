"""Completed UTC daily returns for read-only portfolio diagnostics.

The data adapter must supply:
- measured_at: timezone-aware timestamp
- total_value_usd: portfolio equity
- valuation_status: "usable" only after valuation/accounting checks
- a complete list of external cash-flow timestamps for the supplied period

Cash-flow intervals are excluded, not approximated as investment returns.
This module does not verify historical data availability.
"""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext


def utc_timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("A timezone-aware timestamp is required.")
    return value.astimezone(timezone.utc)


def positive_equity(value):
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() and result > 0 else None


def build_daily_returns(*, points, external_flow_times, as_of):
    cutoff = utc_timestamp(as_of)
    if not isinstance(external_flow_times, (list, tuple)):
        raise ValueError("An explicit external cash-flow timestamp list is required.")
    flows = sorted(utc_timestamp(value) for value in external_flow_times)

    ordered = sorted(
        ((utc_timestamp(point["measured_at"]), point) for point in points),
        key=lambda item: item[0],
    )
    timestamps = [at for at, _ in ordered]
    if len(timestamps) != len(set(timestamps)):
        raise ValueError("Duplicate equity measurement timestamps.")

    boundaries = []
    excluded_points = []
    for at, point in ordered:
        if at >= cutoff:
            reason = "not_completed"
        elif at.time() != time.max:
            reason = "not_utc_daily_boundary"
        else:
            boundaries.append((at, point))
            continue
        excluded_points.append({
            "measured_at": at.isoformat(),
            "reason": reason,
        })

    returns = []
    excluded_intervals = []
    for (start, previous), (end, current) in zip(
        boundaries, boundaries[1:]
    ):
        reasons = []
        if end - start != timedelta(days=1):
            reasons.append("missing_daily_boundary")
        if (
            previous.get("valuation_status") != "usable"
            or current.get("valuation_status") != "usable"
        ):
            reasons.append("unverified_valuation")
        start_value = positive_equity(previous.get("total_value_usd"))
        end_value = positive_equity(current.get("total_value_usd"))
        if start_value is None or end_value is None:
            reasons.append("invalid_or_nonpositive_equity")
        if any(start < when <= end for when in flows):
            reasons.append("external_cash_flow")

        interval = {
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        if reasons:
            excluded_intervals.append({**interval, "reasons": reasons})
            continue

        with localcontext() as context:
            context.prec = 34
            change = end_value / start_value - Decimal("1")
        returns.append({
            **interval,
            "return_fraction": str(change),
        })

    return {
        "methodology": "completed_utc_daily_returns_v1",
        "status": "available" if returns else "insufficient_data",
        "as_of": cutoff.isoformat(),
        "returns": returns,
        "return_count": len(returns),
        "excluded_points": excluded_points,
        "excluded_intervals": excluded_intervals,
        "historical_availability_verified": False,
        "database_writes": False,
        "allocation_authority": False,
        "live_capital_authority": False,
    }
