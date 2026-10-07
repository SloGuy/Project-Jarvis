"""Provider-timed inputs for a future validation integration.

Explicit record IDs must come from a future plan-bound collection.
This selector does not establish that binding or authorize execution.
"""

from datetime import datetime, timedelta, timezone

from app.capital.quote_provenance_eligibility import (
    assess_quote_provenance,
)
from app.capital.quote_provenance_store import (
    load_quote_provenance,
)


def _time(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

    if (
        not isinstance(value, datetime)
        or value.utcoffset() is None
    ):
        raise ValueError(
            "Decision time must be timezone-aware."
        )

    return value.astimezone(timezone.utc)


def select_validation_quote_inputs(
    *,
    directory,
    record_ids,
    asset_id,
    symbol,
    provider,
    decision_at,
    maximum_provider_age,
    maximum_capture_age,
    lookback,
):
    if type(asset_id) is not int or asset_id <= 0:
        raise ValueError("Invalid asset ID.")

    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Invalid symbol.")

    if provider not in {
        "CoinGecko REST",
        "Finnhub REST",
        "Finnhub WebSocket",
    }:
        raise ValueError("Unsupported provider.")

    if type(lookback) is not int or lookback <= 0:
        raise ValueError("Invalid lookback.")

    if not isinstance(record_ids, (list, tuple)):
        raise ValueError(
            "Supply explicit provenance record IDs."
        )

    for limit in (
        maximum_provider_age,
        maximum_capture_age,
    ):
        if (
            not isinstance(limit, timedelta)
            or limit <= timedelta(0)
        ):
            raise ValueError(
                "Age limits must be positive timedeltas."
            )

    decision = _time(decision_at)
    symbol = symbol.strip().upper()
    visible = []
    seen = set()

    for record_id in record_ids:
        # Verify every supplied file, including duplicate IDs.
        record = load_quote_provenance(
            directory=directory,
            record_id=record_id,
        )

        if (
            record["asset_id"] != asset_id
            or record["symbol"] != symbol
            or record["provider"] != provider
        ):
            raise ValueError(
                "Record identity differs from selected series."
            )

        if record_id in seen:
            continue

        seen.add(record_id)

        if _time(record["captured_at"]) < decision:
            visible.append((record_id, record))

    visible.sort(
        key=lambda item: (
            _time(item[1]["captured_at"]),
            item[0],
        )
    )

    result = {
        "status": "unavailable",
        "reason": "no_record_visible_before_decision",
        "decision_at": decision.isoformat(),
        "latest_record_id": None,
        "eligibility": None,
        "observations": [],
        "scope": "provider_timed_input_selection_only",
        "plan_binding_verified": False,
        "execution_authorized": False,
        "live_capital_authorized": False,
    }

    if not visible:
        return result

    # The latest captured response determines current eligibility.
    # Do not hide missing or stale data by using an older response.
    latest_id, latest = visible[-1]

    eligibility = assess_quote_provenance(
        record=latest,
        measured_at=decision,
        maximum_provider_age=maximum_provider_age,
        maximum_capture_age=maximum_capture_age,
    )

    result.update(
        latest_record_id=latest_id,
        eligibility=eligibility,
    )

    # Historical quotes need not be fresh at the current decision.
    # Count repeated provider timestamps once, retaining first visibility.
    unique = {}

    for record_id, record in visible:
        observed = record["provider_observed_at"]

        if observed is None:
            continue

        observed = _time(observed)

        if observed >= decision:
            continue

        if observed in unique:
            previous = unique[observed]

            if previous["price_usd"] != record["price_usd"]:
                raise ValueError(
                    "Conflicting prices at the same provider time."
                )

            continue

        unique[observed] = {
            "price_usd": record["price_usd"],
            "observed_at": observed.isoformat(),
            "first_captured_at": record["captured_at"],
            "record_id": record_id,
        }

    if eligibility["status"] != "eligible":
        result.update(
            status="ineligible",
            reason="timestamp_requirements_not_met",
        )
        return result

    result.update(
        status="eligible",
        reason=None,
        observations=[
            unique[key]
            for key in sorted(
                unique,
                reverse=True,
            )[:lookback]
        ],
    )

    return result
