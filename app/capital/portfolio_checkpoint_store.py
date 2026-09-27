"""Atomic, content-addressed storage for portfolio evidence checkpoints.

Hashes detect changed content; they do not establish historical completeness.
Decimal values retain their type and exact value through explicit encoding.
"""
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

from app.capital.portfolio_daily_returns import utc_timestamp


DECIMAL_TAG = "__checkpoint_decimal__"


def _pack(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Nonfinite checkpoint Decimal.")
        return {DECIMAL_TAG: str(value)}
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Nonfinite checkpoint float.")
        return value
    if isinstance(value, list):
        return [_pack(item) for item in value]
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError("Checkpoint object keys must be strings.")
        if DECIMAL_TAG in value:
            raise ValueError("Reserved checkpoint encoding field.")
        return {key: _pack(item) for key, item in value.items()}
    raise ValueError(f"Unsupported checkpoint type: {type(value).__name__}")


def _unpack(value):
    if isinstance(value, list):
        return [_unpack(item) for item in value]
    if isinstance(value, dict):
        if DECIMAL_TAG in value:
            if set(value) != {DECIMAL_TAG}:
                raise ValueError("Invalid Decimal encoding.")
            raw = value[DECIMAL_TAG]
            if not isinstance(raw, str):
                raise ValueError("Decimal encoding must contain text.")
            try:
                number = Decimal(raw)
            except ArithmeticError as error:
                raise ValueError("Invalid encoded Decimal.") from error
            if not number.is_finite() or str(number) != raw:
                raise ValueError("Noncanonical encoded Decimal.")
            return number
        return {key: _unpack(item) for key, item in value.items()}
    return value


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate checkpoint JSON field.")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Nonfinite checkpoint JSON number.")


def _validate(checkpoint):
    if not isinstance(checkpoint, dict):
        raise ValueError("Checkpoint must be an object.")
    if type(checkpoint.get("schema_version")) is not int:
        raise ValueError("Invalid checkpoint schema version.")
    if checkpoint["schema_version"] != 1:
        raise ValueError("Unsupported checkpoint schema version.")
    if checkpoint.get("methodology") != (
        "prospective_portfolio_evidence_snapshot_v1"
    ):
        raise ValueError("Unsupported checkpoint methodology.")

    for name in (
        "checkpoint_persisted",
        "historical_return_eligible",
        "database_writes",
        "execution_authorized",
        "allocation_authority",
        "live_capital_authority",
    ):
        if checkpoint.get(name) is not False:
            raise ValueError(f"Unexpected checkpoint claim: {name}")

    started = utc_timestamp(checkpoint["started_at"])
    sampled = utc_timestamp(checkpoint["sampled_at"])
    finished = utc_timestamp(checkpoint["finished_at"])
    if not started <= sampled <= finished:
        raise ValueError("Checkpoint timestamps are inconsistent.")

    audit = checkpoint["audit_snapshot"]
    inputs = checkpoint["valuation_inputs"]
    if utc_timestamp(audit["sampled_at"]) != sampled:
        raise ValueError("Audit sampling time differs.")
    if utc_timestamp(inputs["snapshot_at"]) != sampled:
        raise ValueError("Valuation sampling time differs.")
    if (
        inputs["audit_installation_id"]
        != audit["installation"]["installation_id"]
        or inputs["audit_visibility_snapshot"] != audit["visibility_snapshot"]
    ):
        raise ValueError("Checkpoint audit binding differs.")

    for name in (
        "row_reconciliation",
        "accounting_effects",
        "valuations",
        "selected_quote_records",
    ):
        if not isinstance(checkpoint.get(name), dict):
            raise ValueError(f"Missing checkpoint object: {name}")
    if not isinstance(checkpoint.get("resolved_portfolios"), list):
        raise ValueError("Missing portfolio bindings.")


def _encoded(checkpoint):
    _validate(checkpoint)
    envelope = {
        "storage_schema_version": 1,
        "decimal_encoding": DECIMAL_TAG,
        "checkpoint": _pack(checkpoint),
    }
    raw = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return raw, hashlib.sha256(raw).hexdigest()


def load_portfolio_checkpoint(*, directory, record_id):
    if not isinstance(record_id, str) or re.fullmatch(
        r"[0-9a-f]{64}", record_id
    ) is None:
        raise ValueError("Invalid checkpoint record ID.")

    raw = (Path(directory) / f"{record_id}.json").read_bytes()
    envelope = json.loads(
        raw,
        object_pairs_hook=_unique_fields,
        parse_constant=_invalid_constant,
    )
    if not isinstance(envelope, dict) or set(envelope) != {
        "storage_schema_version", "decimal_encoding", "checkpoint"
    }:
        raise ValueError("Invalid checkpoint storage envelope.")
    if (
        type(envelope["storage_schema_version"]) is not int
        or envelope["storage_schema_version"] != 1
        or envelope["decimal_encoding"] != DECIMAL_TAG
    ):
        raise ValueError("Unsupported checkpoint storage encoding.")

    checkpoint = _unpack(envelope["checkpoint"])
    canonical, actual_id = _encoded(checkpoint)
    if actual_id != record_id or canonical != raw:
        raise ValueError("Checkpoint integrity mismatch.")
    return checkpoint


def save_portfolio_checkpoint(*, directory, checkpoint):
    """Publish without overwriting; return a separate persistence receipt.

    The captured payload remains unchanged, including its capture-time
    checkpoint_persisted=False field. This receipt confirms publication.
    """
    raw, record_id = _encoded(checkpoint)
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{record_id}.json"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".portfolio-checkpoint-", suffix=".tmp", dir=root
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            load_portfolio_checkpoint(directory=root, record_id=record_id)

        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        load_portfolio_checkpoint(directory=root, record_id=record_id)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "record_id": record_id,
        "path": str(destination),
        "bytes": len(raw),
        "checkpoint_persisted": True,
        "historical_return_eligible": False,
        "database_writes": False,
        "execution_authorized": False,
    }
