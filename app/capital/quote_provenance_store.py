"""Atomic local storage for quote provenance records.

Identical records share a content-derived filename. Existing records are
verified, never overwritten. This is not a signed or complete event log.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from app.capital.quote_provenance import make_quote_provenance


def _validated_record(record):
    if not isinstance(record, dict):
        raise ValueError("Provenance record must be a dictionary.")

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

    # Canonical serialization also distinguishes booleans from integers.
    try:
        supplied = json.dumps(
            record, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        expected = json.dumps(
            rebuilt, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid provenance record encoding.") from error

    if supplied != expected:
        raise ValueError("Provenance record structure or derived fields differ.")
    return rebuilt


def _encoded(record):
    validated = _validated_record(record)
    raw = json.dumps(
        validated,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return raw, hashlib.sha256(raw).hexdigest()


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field.")
        result[key] = value
    return result


def load_quote_provenance(*, directory, record_id):
    if not isinstance(record_id, str) or re.fullmatch(
        r"[0-9a-f]{64}", record_id
    ) is None:
        raise ValueError("Invalid provenance record ID.")

    path = Path(directory) / f"{record_id}.json"
    raw = path.read_bytes()
    try:
        record = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, UnicodeError) as error:
        raise ValueError("Cannot decode provenance record.") from error

    canonical, actual_id = _encoded(record)
    if actual_id != record_id or raw != canonical:
        raise ValueError("Provenance record integrity mismatch.")
    return record


def _sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save_quote_provenance(*, directory, record):
    """Publish one complete file without replacing an existing record.

    Requires a local filesystem supporting hard links and directory fsync.
    No success is reported if publication or durability checks fail.
    """
    raw, record_id = _encoded(record)
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{record_id}.json"

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".quote-provenance-",
        suffix=".tmp",
        dir=root,
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
            # Another writer or an earlier identical capture may exist.
            # Corruption must fail; it must never trigger replacement.
            load_quote_provenance(directory=root, record_id=record_id)

        _sync_directory(root)
        load_quote_provenance(directory=root, record_id=record_id)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "record_id": record_id,
        "path": str(destination),
    }
