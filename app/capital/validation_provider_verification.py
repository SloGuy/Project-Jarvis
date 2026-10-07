"""Offline reconstruction of embedded provider-time evidence.

Hashes establish consistency with retained references, not independent
provider authentication. No database or registry access is performed.
"""

from contextlib import contextmanager
from dataclasses import asdict
import json
import tempfile

from app.capital.observation_witness import digest
from app.capital.quote_provenance_store import save_quote_provenance
from app.capital.validation_input_contract import validate_input_contract
from app.capital.validation_plan import timestamp, verify_plan
from app.capital.validation_provider_replay import ProviderReplayHistory
from app.capital.validation_quote_receipt import (
    verify_validation_quote_receipt,
)
from app.capital.validation_quote_store import MAX_RECEIPTS


PACKET_FIELDS = {
    "schema_version",
    "envelope",
    "registered_sha256",
    "collection",
    "receipts",
}

COLLECTION_FIELDS = {
    "schema_version",
    "kind",
    "plan_sha256",
    "bound_at",
    "status",
    "store_checkpoint",
    "sha256",
}

CHECKPOINT_FIELDS = {
    "schema_version",
    "binding",
    "count",
    "head",
    "sealed",
    "sha256",
}


def normalized(value):
    return json.loads(
        json.dumps(value, default=str, allow_nan=False)
    )


def same(left, right):
    return json.dumps(
        normalized(left), sort_keys=True, separators=(",", ":")
    ) == json.dumps(
        normalized(right), sort_keys=True, separators=(",", ":")
    )


def check_digest(value):
    body = {
        key: item
        for key, item in value.items()
        if key != "sha256"
    }
    if digest(body) != value["sha256"]:
        raise ValueError("Embedded evidence integrity mismatch.")


@contextmanager
def open_provider_packet(
    packet,
    *,
    expected_sha256,
    expected_collection=None,
):
    if (
        not isinstance(packet, dict)
        or set(packet) != PACKET_FIELDS
        or type(packet["schema_version"]) is not int
        or packet["schema_version"] != 1
        or packet["registered_sha256"] != expected_sha256
    ):
        raise ValueError("Invalid provider evidence packet.")

    plan = verify_plan(
        packet["envelope"],
        expected_sha256=expected_sha256,
    )

    if plan["schema_version"] != 2:
        raise ValueError("Provider evidence requires a version-two plan.")

    validate_input_contract(
        plan["input_contract"],
        policy=plan["policy"],
        verify_sources=True,
    )

    collection = packet["collection"]

    if (
        not isinstance(collection, dict)
        or set(collection) != COLLECTION_FIELDS
        or type(collection["schema_version"]) is not int
        or collection["schema_version"] != 1
        or collection["kind"] != "provider_time_v1"
        or collection["plan_sha256"] != expected_sha256
        or collection["status"] != "sealed"
    ):
        raise ValueError("Invalid sealed provider collection.")

    check_digest(collection)

    if (
        expected_collection is not None
        and not same(collection, expected_collection)
    ):
        raise ValueError("Collection differs from retained reference.")

    bound = timestamp(collection["bound_at"])

    if not (
        timestamp(plan["created_at"])
        <= bound
        < timestamp(plan["start"])
    ):
        raise ValueError("Invalid prospective collection binding.")

    checkpoint = collection["store_checkpoint"]

    if (
        not isinstance(checkpoint, dict)
        or set(checkpoint) != CHECKPOINT_FIELDS
        or type(checkpoint["schema_version"]) is not int
        or checkpoint["schema_version"] != 1
        or type(checkpoint["count"]) is not int
        or not 1 <= checkpoint["count"] <= MAX_RECEIPTS
        or checkpoint["sealed"] is not True
    ):
        raise ValueError("Invalid sealed provider checkpoint.")

    check_digest(checkpoint)

    binding = {
        "receipt_type": "provider_time_v1",
        "plan_sha256": expected_sha256,
        "bound_at": bound.isoformat(),
    }

    if not same(checkpoint["binding"], binding):
        raise ValueError("Provider checkpoint binding mismatch.")

    receipts = packet["receipts"]

    if (
        not isinstance(receipts, list)
        or len(receipts) != checkpoint["count"]
    ):
        raise ValueError("Provider receipt count mismatch.")

    previous = None
    previous_time = bound
    payloads = []

    for index, receipt in enumerate(receipts, start=1):
        payload = verify_validation_quote_receipt(
            receipt=receipt,
            envelope=packet["envelope"],
            expected_sha256=expected_sha256,
            expected_bound_at=bound.isoformat(),
        )

        recorded = timestamp(payload["recorded_at"])

        if recorded <= previous_time:
            raise ValueError("Receipt logging times must increase.")

        body = {
            "index": index,
            "previous": previous,
            "receipt": receipt,
        }
        previous = digest(body)
        previous_time = recorded
        payloads.append(payload)

    if previous != checkpoint["head"]:
        raise ValueError("Embedded provider chain head mismatch.")

    with tempfile.TemporaryDirectory(
        prefix="jarvis-provider-replay-"
    ) as directory:
        for payload in payloads:
            saved = save_quote_provenance(
                directory=directory,
                record=payload["record"],
            )
            if saved["record_id"] != payload["record_id"]:
                raise ValueError("Embedded quote identity mismatch.")

        yield ProviderReplayHistory(
            directory=directory,
            receipts=receipts,
            envelope=packet["envelope"],
            expected_sha256=expected_sha256,
            expected_bound_at=bound.isoformat(),
        )


def verify_provider_inputs(report):
    if (
        "witness_collection" in report
        or "witness_evidence" in report
    ):
        raise ValueError("Legacy and provider evidence cannot be mixed.")

    registration = report.get("validation_registration")

    if (
        not isinstance(registration, dict)
        or set(registration) != {"plan_id", "sha256"}
        or not isinstance(registration["plan_id"], str)
        or not registration["plan_id"].strip()
    ):
        raise ValueError("Provider report requires registration.")

    claimed = report.get("availability_verified")

    if type(claimed) is not bool:
        raise ValueError("Availability flag must be boolean.")

    windows = report["windows"]

    if not isinstance(windows, list) or not windows:
        raise ValueError("Provider report has no decision windows.")

    with open_provider_packet(
        report["provider_evidence"],
        expected_sha256=registration["sha256"],
    ) as history:
        plan = history.plan

        if report.get("designation") != "prospective_validation":
            raise ValueError("Invalid provider report designation.")

        for field in (
            "asset_id",
            "symbol",
            "provider",
            "start",
            "end_exclusive",
            "policy",
            "execution_manifest",
        ):
            if not same(report.get(field), plan[field]):
                raise ValueError(
                    "Provider report differs from plan: " + field
                )

        scenarios = report["scenarios"]

        if set(scenarios) != {"zero_cost", "specified_costs"}:
            raise ValueError("Registered cost scenarios are required.")

        for name, fee, slippage in (
            ("zero_cost", "0", "0"),
            (
                "specified_costs",
                plan["fee_bps"],
                plan["slippage_bps"],
            ),
        ):
            from decimal import Decimal

            scenario = scenarios[name]

            if (
                Decimal(str(scenario["fee_bps"])) != Decimal(fee)
                or Decimal(str(scenario["slippage_bps"]))
                != Decimal(slippage)
            ):
                raise ValueError("Scenario costs differ from plan.")

        all_witnessed = True

        for index, saved in enumerate(windows):
            rebuilt = history.window(
                decision_at=saved["decision_at"]
            )

            snapshot = asdict(rebuilt["snapshot"])
            observed = rebuilt["snapshot"].observation_at
            snapshot["observation_at"] = (
                observed.isoformat() if observed else None
            )

            if not same(
                snapshot,
                saved["snapshot"],
            ):

                raise ValueError(
                    f"Provider snapshot mismatch at tick {index}."
                )

            if not same(
                rebuilt["observation_ids"],
                saved.get("observation_ids"),
            ):
                raise ValueError(
                    f"Provider record IDs mismatch at tick {index}."
                )

            if not same(
                rebuilt["provider_input"],
                saved.get("provider_input"),
            ):
                raise ValueError(
                    f"Provider input evidence mismatch at tick {index}."
                )

            all_witnessed = (
                all_witnessed
                and rebuilt["selected_values_witnessed"]
            )

        if claimed != all_witnessed:
            raise ValueError(
                "Availability flag differs from provider evidence."
            )

    return all_witnessed
