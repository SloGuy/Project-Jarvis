"""Bounded development replay of a shared two-strategy portfolio."""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

from app.market_db.database import SessionLocal
from app.capital.shadow_inputs import load_shadow_inputs, shadow_configuration
from app.capital.shadow_risk import controls, downward_volatility_scale
from app.capital.shadow_checkpoint import (
    create_checkpoint, process_tick, verify_checkpoint,
    normalized, encode_tick, locked, read_checkpoint,
)

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIRECTORY = ROOT / "work/shared_shadow"
INPUT_SOURCES = (
    "app/capital/run_shared_shadow.py",
    "app/capital/shadow_inputs.py",
    "app/capital/shadow_risk.py",
    "app/capital/allocator.py",
    "app/capital/allocation_policy.py",
)


def source_hashes():
    return {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in INPUT_SOURCES
    }


def save(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(normalized(value), handle, indent=2, allow_nan=False)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("Timezone is required.")
    return result.astimezone(timezone.utc)


def run_shared(*, allocation, assets, start, end, fee_bps, slippage_bps,
               risk_mode="normal", target_volatility_percent=None):
    if start.utcoffset() is None or end.utcoffset() is None:
        raise ValueError("Timezone-aware start and end are required.")
    seconds = (end - start).total_seconds()
    if seconds <= 0 or seconds % 60 or seconds > 360 * 60:
        raise ValueError("Use 1–360 whole-minute ticks.")
    if end > datetime.now(timezone.utc):
        raise ValueError("Development replay must end in the past.")
    if not assets or len(set(assets)) != len(assets):
        raise ValueError("Provide distinct asset/provider pairs.")
    policy, caps = shadow_configuration(allocation)
    controls(caps, risk_mode, {})
    if target_volatility_percent is not None:
        target_volatility_percent = Decimal(str(target_volatility_percent))
        downward_volatility_scale(
            [], target_volatility_percent=target_volatility_percent
        )
    directory = OUTPUT_DIRECTORY / uuid4().hex
    directory.mkdir(parents=True)
    fingerprints = source_hashes()
    hashes = {}
    hashes["allocation.json"] = save(directory / "allocation.json", allocation)
    hashes["plan.json"] = save(directory / "plan.json", {
        "designation": "development",
        "assets": assets, "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "fee_bps": fee_bps, "slippage_bps": slippage_bps,
        "input_sources": fingerprints,
        "risk_mode": risk_mode,
        "target_observation_volatility_percent": target_volatility_percent,

        "allocation_sha256": hashes["allocation.json"],
        "allocation_known_at_historical_time": False,
        "historical_availability_verified": False,
    })
    print("Shared replay directory:", directory, flush=True)
    try:
        create_checkpoint(
            directory / "checkpoint", policy=policy, strategy_caps=caps,
            fee_bps=fee_bps, slippage_bps=slippage_bps,
        )
        ticks, provenance = [], []
        with SessionLocal() as session:
            session.execute(text(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            ))
            session.execute(text("SET LOCAL statement_timeout = '30s'"))
            for index in range(int(seconds / 60)):
                at = start + timedelta(minutes=index)
                snapshots, quotes, sources = {}, {}, []
                scales = {name: Decimal("1") for name in caps}
                for asset_id, provider in assets:
                    loaded = load_shadow_inputs(
                        session, asset_id=asset_id, provider=provider, decision_at=at
                    )
                    symbol = loaded["symbol"]
                    if symbol in quotes:
                        raise ValueError("Asset selections resolve to duplicate symbols.")
                    if loaded["quote"] is None:
                        raise ValueError(f"No reference quote for {symbol} at {at}.")
                    snapshots.update(loaded["snapshots"])
                    quotes[symbol] = loaded["quote"]
                    volatility = None
                    if target_volatility_percent is not None:
                        volatility = downward_volatility_scale(
                            loaded["chronological_prices"],
                            target_volatility_percent=target_volatility_percent,
                        )
                        for name in scales:
                            scales[name] = min(scales[name], volatility["scale"])
                    sources.append({
                        "volatility_scaling": volatility,
                        "asset_id": asset_id, "provider": provider, "symbol": symbol,
                        "observation_ids": loaded["observation_ids"],
                        "observation_times": loaded["observation_times"],
                    })
                ticks.append({
                    "decision_at": at, "snapshots": snapshots, "quotes": quotes,
                    "risk_only": index % 5 != 0,
                    "risk_mode": risk_mode, "size_scales": scales,
                })
                provenance.append({"decision_at": at.isoformat(), "sources": sources})
        hashes["market_inputs.json"] = save(directory / "market_inputs.json", {
            "ticks": [encode_tick(**tick) for tick in ticks],
            "provenance": provenance,
        })
        if fingerprints != source_hashes():
            raise ValueError("Shadow input code changed during data capture.")
        for tick in ticks:
            process_tick(directory / "checkpoint", **tick)
        verification = verify_checkpoint(directory / "checkpoint")
        if fingerprints != source_hashes():
            raise ValueError("Shadow input code changed during replay.")
        with locked(directory / "checkpoint") as path:
            state = read_checkpoint(path)
            checkpoint_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        account = verification["last_result"]["account"]
        events = [
            event for record in state["ticks"]
            for event in record["result"]["events"]
        ]
        summary = {
            "mode": "development_shared_shadow",
            "ticks": verification["ticks"],
            "strategies": sorted(caps),
            "risk_mode": risk_mode,
            "target_observation_volatility_percent": target_volatility_percent,
            "sizing_scale_range": {
                name: {
                    "minimum": min(tick["size_scales"][name] for tick in ticks),
                    "maximum": max(tick["size_scales"][name] for tick in ticks),
                }
                for name in caps
            },
            "executed_orders": sum(event["executed"] for event in events),
            "rejected_orders": sum(not event["executed"] for event in events),
            "account": account,
            "checkpoint_sha256": checkpoint_hash,
            "verification_status": verification["status"],
            "paper_execution_authority": False,
            "live_capital_authority": False,
            "historical_availability_verified": False,
            "limitations": [
                "Allocation snapshot is not proven known at historical decision time.",
                "Synthetic schedule and assumed fills; no live execution parity.",
                "Sampled reference marks exclude future liquidation costs.",
                "Volatility uses observation returns with potentially irregular spacing.",
                "Scaling is a shadow comparison, not a calibrated risk forecast.",
            ],
        }
        hashes["summary.json"] = save(directory / "summary.json", summary)
        save(directory / "result.json", {
            "status": "completed", "artifacts_sha256": hashes,
            "checkpoint_sha256": checkpoint_hash,
            "promotion_authorized": False,
        })
        print(
            "Ticks:", summary["ticks"],
            "executed:", summary["executed_orders"],
            "rejected:", summary["rejected_orders"],
            "equity:", account["total_value_usd"],
        )
        for strategy, contribution in account["strategy_attribution"].items():
            print(strategy, contribution)
        print("PASS: shared shadow checkpoint replay matched.")
        return directory
    except Exception as error:
        save(directory / "failure.json", {
            "status": "failed", "error_type": type(error).__name__,
            "message": str(error), "completed_artifacts_sha256": hashes,
        })
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--allocation", type=Path, required=True)
    parser.add_argument("--asset", action="append", required=True,
                        help="Asset ID and provider, for example 1:Finnhub")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--fee-bps", type=Decimal, required=True)
    parser.add_argument("--slippage-bps", type=Decimal, required=True)
    parser.add_argument(
        "--risk-mode", choices=["normal", "reduce_only", "halted"], default="normal"
    )
    parser.add_argument("--target-volatility-percent", type=Decimal)
    args = parser.parse_args()
    assets = []
    for value in args.asset:
        ident, provider = value.split(":", 1)
        assets.append((int(ident), provider))
    run_shared(
        allocation=json.loads(args.allocation.read_text()), assets=assets,
        start=parse_time(args.start), end=parse_time(args.end),
        fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
        risk_mode=args.risk_mode,
        target_volatility_percent=args.target_volatility_percent,
    )


if __name__ == "__main__":
    main()
