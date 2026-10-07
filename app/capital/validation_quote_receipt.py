"""Plan-bound provenance receipts for future validation tooling.

Separate from legacy observation receipts and their active collections.
Hashes detect changes; they do not authenticate timestamps or providers.
This module does not register plans or authorize execution.
"""

import hashlib
import json

from app.capital.quote_provenance import (
    make_quote_provenance,
)
from app.capital.quote_provenance_store import (
    load_quote_provenance,
)
from app.capital.validation_plan import (
    canonical,
    plan_digest,
    timestamp,
    verify_plan,
)


PROVIDERS = {
    "CoinGecko": "CoinGecko REST",
    "Finnhub": "Finnhub REST",
}

PAYLOAD_FIELDS = {
    "schema_version",
    "plan_sha256",
    "bound_at",
    "recorded_at",
    "record_id",
    "record",
}


def _validate_payload(payload, plan, expected_sha256):
    if (
        not isinstance(payload, dict)
        or set(payload) != PAYLOAD_FIELDS
    ):
        raise ValueError("Unexpected provenance receipt fields.")

    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
    ):
        raise ValueError("Unsupported provenance receipt schema.")

    if payload["plan_sha256"] != expected_sha256:
        raise ValueError("Receipt belongs to another plan.")

    created = timestamp(plan["created_at"])
    start = timestamp(plan["start"])
    end = timestamp(plan["end_exclusive"])
    bound = timestamp(payload["bound_at"])
    recorded = timestamp(payload["recorded_at"])

    if not created <= bound < start:
        raise ValueError(
            "Collection must bind after plan creation and before start."
        )

    if not bound < recorded < end:
        raise ValueError("Receipt logging time is outside collection.")

    record = payload["record"]

    if not isinstance(record, dict):
        raise ValueError("Receipt record must be a dictionary.")

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

    if canonical(record) != canonical(rebuilt):
        raise ValueError(
            "Provenance structure or derived fields differ."
        )

    # Match the content-derived ID used by quote_provenance_store.
    raw = json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    record_id = hashlib.sha256(raw).hexdigest()

    if payload["record_id"] != record_id:
        raise ValueError("Provenance record ID mismatch.")

    expected_provider = PROVIDERS.get(plan["provider"])

    if expected_provider is None:
        raise ValueError("Unsupported validation provider.")

    if (
        record["asset_id"] != plan["asset_id"]
        or record["symbol"] != plan["symbol"].strip().upper()
        or record["provider"] != expected_provider
    ):
        raise ValueError("Quote identity differs from plan.")

    captured = timestamp(record["captured_at"])

    if not bound <= captured <= recorded:
        raise ValueError(
            "Quote must be captured after binding and before logging."
        )

    return payload


def make_validation_quote_receipt(
    *,
    envelope,
    expected_sha256,
    bound_at,
    recorded_at,
    directory,
    record_id,
):
    """Build a receipt from an integrity-checked provenance file.

    recorded_at must be sampled by the future collection writer.
    Supplying a timestamp here does not independently attest its truth.
    """
    plan = verify_plan(
        envelope,
        expected_sha256=expected_sha256,
    )

    record = load_quote_provenance(
        directory=directory,
        record_id=record_id,
    )

    payload = {
        "schema_version": 1,
        "plan_sha256": expected_sha256,
        "bound_at": timestamp(bound_at).isoformat(),
        "recorded_at": timestamp(recorded_at).isoformat(),
        "record_id": record_id,
        "record": record,
    }

    _validate_payload(
        payload,
        plan,
        expected_sha256,
    )

    return {
        "payload": payload,
        "sha256": plan_digest(payload),
    }


def verify_validation_quote_receipt(
    *,
    receipt,
    envelope,
    expected_sha256,
    expected_bound_at,
):
    """Verify a receipt against separately retained plan and binding."""
    plan = verify_plan(
        envelope,
        expected_sha256=expected_sha256,
    )

    if (
        not isinstance(receipt, dict)
        or set(receipt) != {"payload", "sha256"}
    ):
        raise ValueError("Invalid provenance receipt envelope.")

    payload = _validate_payload(
        receipt["payload"],
        plan,
        expected_sha256,
    )

    if (
        timestamp(payload["bound_at"])
        != timestamp(expected_bound_at)
    ):
        raise ValueError("Collection binding time differs.")

    if receipt["sha256"] != plan_digest(payload):
        raise ValueError("Provenance receipt integrity mismatch.")

    # Return an independent copy rather than the caller's mutable object.
    return json.loads(canonical(payload))
