"""Reconstruct replay inputs from embedded visibility receipts."""
from dataclasses import asdict
import json

from app.capital.witnessed_history import WitnessedHistory


def normalized(value):
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def verify_witness_inputs(report):
    evidence = report.get("witness_evidence")
    claimed = report.get("availability_verified", False)
    if type(claimed) is not bool:
        raise ValueError("Availability flag must be boolean.")
    if evidence is None:
        if claimed:
            raise ValueError("Availability claim requires witness evidence.")
        return False

    if (
        not isinstance(evidence, dict)
        or set(evidence) != {"schema_version", "receipts"}
        or type(evidence["schema_version"]) is not int
        or evidence["schema_version"] != 1
        or not isinstance(evidence["receipts"], list)
    ):
        raise ValueError("Invalid witness evidence structure.")

    collection = report.get("witness_collection")
    if collection is not None:
        from app.capital.validation_collection import validate_collection
        registration = report.get("validation_registration", {})
        validate_collection(collection, registration.get("sha256"))
        if (
            collection["status"] != "sealed"
            or collection["receipts"] != evidence["receipts"]
        ):
            raise ValueError("Witness evidence differs from sealed collection.")
    history = WitnessedHistory(evidence["receipts"])
    windows = report["windows"]
    if not windows:
        raise ValueError("Witness report has no decision windows.")

    all_witnessed = True
    for index, saved in enumerate(windows):
        rebuilt = history.window(
            asset_id=report["asset_id"],
            provider=report["provider"],
            symbol=report["symbol"],
            decision_at=saved["decision_at"],
        )
        snapshot = asdict(rebuilt["snapshot"])
        observed = rebuilt["snapshot"].observation_at
        snapshot["observation_at"] = (
            observed.isoformat() if observed else None
        )
        if normalized(snapshot) != saved["snapshot"]:
            raise ValueError(
                f"Witness snapshot mismatch at tick {index}."
            )
        if rebuilt["observation_ids"] != saved.get("observation_ids"):
            raise ValueError(
                f"Witness observation IDs mismatch at tick {index}."
            )
        all_witnessed = (
            all_witnessed and rebuilt["selected_values_witnessed"]
        )

    if claimed != all_witnessed:
        raise ValueError("Availability flag differs from witness evidence.")
    return all_witnessed
