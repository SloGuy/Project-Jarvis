"""Explicit timestamp eligibility for prospective quote provenance.

This check does not certify provider authenticity or complete history.
It applies elapsed-time limits without inferring exchange sessions.
"""

from datetime import timedelta

from app.capital.quote_provenance import make_quote_provenance
from app.capital.portfolio_daily_returns import utc_timestamp


def assess_quote_provenance(
    *,
    record,
    measured_at,
    maximum_provider_age,
    maximum_capture_age,
):
    """Assess one record at a specified measurement time.

    Callers loading persisted records must use load_quote_provenance first.
    Thresholds are explicit; stock-market closures are not special-cased.
    """
    if not isinstance(record, dict):
        raise ValueError("Record must be a dictionary.")

    for name, value in (
        ("maximum_provider_age", maximum_provider_age),
        ("maximum_capture_age", maximum_capture_age),
    ):
        if not isinstance(value, timedelta) or value <= timedelta(0):
            raise ValueError(f"{name} must be a positive timedelta.")

    try:
        rebuilt = make_quote_provenance(
            asset_id=record["asset_id"],
            symbol=record["symbol"],
            asset_type=record["asset_type"],
            provider=record["provider"],
            price_usd=record["price_usd"],
            provider_timestamp=record["provider_timestamp_raw"],
            captured_at=record["captured_at"],
        )
    except KeyError as error:
        raise ValueError("Incomplete provenance record.") from error

    # Compare canonical encodings to reject extra fields and type changes.
    import json

    if json.dumps(record, sort_keys=True, allow_nan=False) != json.dumps(
        rebuilt, sort_keys=True, allow_nan=False
    ):
        raise ValueError("Provenance structure or derived fields differ.")

    measured = utc_timestamp(measured_at)
    captured = utc_timestamp(record["captured_at"])
    provider_at = (
        utc_timestamp(record["provider_observed_at"])
        if record["provider_observed_at"] is not None else None
    )

    reasons = []
    capture_age = measured - captured

    if capture_age < timedelta(0):
        reasons.append("captured_after_measurement")
    elif capture_age > maximum_capture_age:
        reasons.append("capture_too_old")

    provider_age = None
    if provider_at is None:
        reasons.append("provider_timestamp_missing")
    else:
        provider_age = measured - provider_at
        if provider_age < timedelta(0):
            reasons.append("provider_time_after_measurement")
        elif provider_age > maximum_provider_age:
            reasons.append("provider_quote_too_old")

    return {
        "schema_version": 1,
        "asset_id": record["asset_id"],
        "symbol": record["symbol"],
        "provider": record["provider"],
        "measured_at": measured.isoformat(),
        "provider_observed_at": record["provider_observed_at"],
        "captured_at": record["captured_at"],
        "status": "eligible" if not reasons else "ineligible",
        "reasons": reasons,
        "provider_age_seconds": (
            provider_age.total_seconds() if provider_age is not None else None
        ),
        "capture_age_seconds": capture_age.total_seconds(),
        "maximum_provider_age_seconds": maximum_provider_age.total_seconds(),
        "maximum_capture_age_seconds": maximum_capture_age.total_seconds(),
        "scope": "timestamp_eligibility_only",
        "exchange_session_verified": False,
        "provider_authenticity_verified": False,
        "historical_completeness_verified": False,
        "execution_authorized": False,
        "live_capital_authorized": False,
    }
