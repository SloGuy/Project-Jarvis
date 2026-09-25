"""Read-only coverage reporting for separate quote provenance records."""

from pathlib import Path
import re

from app.capital.portfolio_daily_returns import utc_timestamp
from app.capital.quote_provenance import PROVIDER_CONTRACTS
from app.capital.quote_provenance_eligibility import assess_quote_provenance
from app.capital.quote_provenance_store import load_quote_provenance


def get_quote_provenance_coverage(
    *,
    directory,
    requested_assets,
    measured_at,
    maximum_provider_age,
    maximum_capture_age,
):
    """Report latest capture coverage without selecting an older good quote.

    requested_assets contains asset_id, symbol, asset_type, and provider.
    Every persisted JSON record is integrity-checked. Corruption raises an
    error rather than silently disappearing from the coverage report.

    Records captured after measured_at are excluded. Same-time conflicting
    records are reported as ambiguous rather than arbitrarily selected.
    """
    from datetime import timedelta

    for limit in (maximum_provider_age, maximum_capture_age):
        if not isinstance(limit, timedelta) or limit <= timedelta(0):
            raise ValueError("Age limits must be positive timedeltas.")

    measured = utc_timestamp(measured_at)
    expected = {}

    for asset in requested_assets:
        asset_id = asset["asset_id"]
        provider = asset["provider"]
        symbol = asset["symbol"]
        asset_type = asset["asset_type"]

        if type(asset_id) is not int or asset_id <= 0:
            raise ValueError("Asset IDs must be positive integers.")
        if not isinstance(provider, str) or provider not in PROVIDER_CONTRACTS:
            raise ValueError("Unsupported provider.")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("Symbol must be nonempty.")
        if asset_type != PROVIDER_CONTRACTS[provider][0]:
            raise ValueError("Asset type and provider differ.")

        key = (asset_id, provider)
        if key in expected:
            raise ValueError("Duplicate requested asset/provider.")

        expected[key] = {
            "asset_id": asset_id,
            "symbol": symbol.strip().upper(),
            "asset_type": asset_type,
            "provider": provider,
        }

    root = Path(directory)
    if root.exists() and not root.is_dir():
        raise ValueError("Provenance storage path is not a directory.")

    candidates = {key: [] for key in expected}
    verified_count = 0
    future_count = 0

    for path in sorted(root.glob("*.json")):
        if re.fullmatch(r"[0-9a-f]{64}", path.stem) is None:
            raise ValueError("Unexpected provenance JSON filename.")

        record = load_quote_provenance(
            directory=root,
            record_id=path.stem,
        )
        verified_count += 1
        captured = utc_timestamp(record["captured_at"])
        if captured > measured:
            future_count += 1
            continue

        key = (record["asset_id"], record["provider"])
        if key not in expected:
            continue
        asset = expected[key]
        if (
            record["symbol"] != asset["symbol"]
            or record["asset_type"] != asset["asset_type"]
        ):
            raise ValueError("Saved provenance identity differs from registry.")
        candidates[key].append((captured, path.stem, record))

    rows = []
    for key, asset in sorted(expected.items()):
        records = candidates[key]
        row = {
            **asset,
            "status": "missing",
            "record_id": None,
            "assessment": None,
        }

        if records:
            latest_at = max(item[0] for item in records)
            latest = [item for item in records if item[0] == latest_at]
            if len(latest) != 1:
                row["status"] = "ambiguous"
            else:
                _, record_id, record = latest[0]
                assessment = assess_quote_provenance(
                    record=record,
                    measured_at=measured,
                    maximum_provider_age=maximum_provider_age,
                    maximum_capture_age=maximum_capture_age,
                )
                row.update(
                    status=assessment["status"],
                    record_id=record_id,
                    assessment=assessment,
                )
        rows.append(row)

    return {
        "schema_version": 1,
        "measured_at": measured.isoformat(),
        "status": (
            "eligible"
            if rows and all(row["status"] == "eligible" for row in rows)
            else "incomplete"
        ),
        "requested_count": len(rows),
        "verified_record_count": verified_count,
        "future_capture_count": future_count,
        "assets": rows,
        "scope": "saved_record_integrity_and_timestamp_coverage",
        "limitations": [
            "Coverage is a local filesystem scan, not a transactional snapshot.",
            "Capture timestamps do not establish when records were persisted.",
            "Hashes do not establish provider authenticity or complete history.",
            "Eligibility applies explicit age limits without exchange calendars.",
        ],
        "database_writes": False,
        "execution_authorized": False,
        "live_capital_authorized": False,
    }
