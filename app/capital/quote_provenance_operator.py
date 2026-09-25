"""One-shot quote provenance capture for an explicitly selected asset."""

import argparse
import json
import os
from pathlib import Path
import sys

from app.capital.quote_provenance_capture import capture_quote_provenance


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROVENANCE_DIRECTORY = PROJECT_ROOT / "runtime" / "capital" / "quote_provenance"


def read_asset(asset_id):
    if type(asset_id) is not int or asset_id <= 0:
        raise ValueError("Asset ID must be a positive integer.")

    from sqlalchemy import select, text
    from app.market_db.database import engine
    from app.market_db.models import MarketAsset

    with engine.connect() as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            row = connection.execute(
                select(
                    MarketAsset.id,
                    MarketAsset.symbol,
                    MarketAsset.asset_type,
                    MarketAsset.provider_id,
                    MarketAsset.is_active,
                ).where(MarketAsset.id == asset_id)
            ).mappings().one_or_none()

    if row is None:
        raise ValueError("Asset was not found.")
    if row["is_active"] is not True:
        raise ValueError("Asset is inactive.")
    if row["asset_type"] not in ("stock", "crypto"):
        raise ValueError("Unsupported asset type.")
    return dict(row)


def run_capture(asset_id):
    asset = read_asset(asset_id)

    # The database module loads the project's environment configuration.
    api_key = os.getenv("FINNHUB_API_KEY", "").strip()

    result = capture_quote_provenance(
        asset=asset,
        directory=PROVENANCE_DIRECTORY,
        finnhub_api_key=api_key,
    )
    return {
        "status": "captured",
        **result,
        "database_writes": False,
        "legacy_observation_writes": False,
        "validation_receipt_writes": False,
        "execution_authorized": False,
        "live_capital_authorized": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Capture one quote into separate provenance storage."
    )
    parser.add_argument("--asset-id", type=int, required=True)
    args = parser.parse_args(argv)

    try:
        result = run_capture(args.asset_id)
    except Exception:
        # Request exceptions can contain credential-bearing URLs.
        # Never print the exception, its repr, or its traceback here.
        print(
            json.dumps({
                "status": "failed",
                "asset_id": args.asset_id,
                "message": (
                    "Capture failed during asset lookup, provider retrieval, "
                    "record validation, or storage. No successful capture "
                    "is claimed; a record may exist if publication preceded "
                    "a storage error."
                ),
            }),
            file=sys.stderr,
        )
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
