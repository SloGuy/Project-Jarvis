"""Conservative observation visibility receipts.

A receipt records that these exact values were readable by witnessed_at.
It does not establish first availability, feed completeness, or authenticity
against an administrator who can rewrite both the receipt and its hash.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4


def timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("An aware timestamp is required.")
    return value.astimezone(timezone.utc)


def digest(value):
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def make_receipt(rows, witnessed_at):
    witnessed = timestamp(witnessed_at)
    observations = []
    seen = set()
    for row in rows:
        ident = row["id"]
        asset = row["asset_id"]
        if type(ident) is not int or ident <= 0 or ident in seen:
            raise ValueError("Invalid or duplicate observation ID.")
        if type(asset) is not int or asset <= 0:
            raise ValueError("Invalid asset ID.")
        seen.add(ident)
        observed = timestamp(row["observed_at"])
        if observed > witnessed:
            raise ValueError("Observation timestamp is after witness time.")
        price = Decimal(str(row["price_usd"]))
        if not price.is_finite() or price <= 0:
            raise ValueError("Invalid price.")
        provider = row["provider"]
        if provider not in {"Finnhub", "CoinGecko"}:
            raise ValueError("Unsupported provider.")
        observations.append({
            "id": ident,
            "asset_id": asset,
            "provider": provider,
            "price_usd": str(price),
            "observed_at": observed.isoformat(),
        })
    if not observations:
        raise ValueError("Cannot witness an empty result.")
    payload = {
        "schema_version": 1,
        "witnessed_at": witnessed.isoformat(),
        "observations": sorted(observations, key=lambda row: row["id"]),
        "scope": "Exact values readable no later than witnessed_at.",
    }
    return {"payload": payload, "sha256": digest(payload)}


def verify_receipt(receipt):
    if set(receipt) != {"payload", "sha256"}:
        raise ValueError("Invalid receipt envelope.")
    payload = receipt["payload"]
    rebuilt = make_receipt(
        payload["observations"], payload["witnessed_at"]
    )
    if rebuilt != receipt:
        raise ValueError("Receipt integrity or structure mismatch.")
    return payload


def capture(asset_id, provider, limit=60):
    from sqlalchemy import select, text
    from app.market_db.database import SessionLocal
    from app.market_db.models import PriceObservation as Observation

    if type(asset_id) is not int or asset_id <= 0:
        raise ValueError("Invalid asset ID.")
    if provider not in {"Finnhub", "CoinGecko"}:
        raise ValueError("Unsupported provider.")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Limit must be between 1 and 1000.")

    with SessionLocal() as session:
        session.execute(text(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        ))
        session.execute(text("SET LOCAL statement_timeout = '30s'"))
        rows = session.execute(
            select(
                Observation.id, Observation.asset_id,
                Observation.provider, Observation.price_usd,
                Observation.observed_at,
            )
            .where(
                Observation.asset_id == asset_id,
                Observation.provider == provider,
            )
            .order_by(
                Observation.observed_at.desc(), Observation.id.desc()
            )
            .limit(limit)
        ).mappings().all()
        # PostgreSQL wall clock, sampled after the result was retrieved.
        witnessed = session.scalar(text("SELECT clock_timestamp()"))
        receipt = make_receipt(rows, witnessed)

    directory = (
        Path(__file__).resolve().parents[2]
        / "runtime/capital/observation_witnesses"
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid4().hex}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    verify_receipt(json.loads(path.read_text()))
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-id", type=int, required=True)
    parser.add_argument(
        "--provider", choices=["Finnhub", "CoinGecko"], required=True
    )
    parser.add_argument("--limit", type=int, default=60)
    args = parser.parse_args()
    print("Witness receipt:", capture(
        args.asset_id, args.provider, args.limit
    ))
    print("Database writes: NONE")
    print("Replay availability flags: UNCHANGED")


if __name__ == "__main__":
    main()
