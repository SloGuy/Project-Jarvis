"""Run one bounded development evaluation with an evidence packet."""
import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, text

from app.market_db.database import SessionLocal
from app.market_db.models import MarketAsset
from app.capital.historical_observations import (
    COLLECTED_PROVIDERS, load_historical_snapshot,
)
from app.capital.position_simulation import PositionSimulation
from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY as POLICY
from app.capital.replay_manifest import (
    capture_replay_manifest, verify_replay_manifest,
)
from app.capital.offline_verification import verify_report
from app.capital.replay_analysis import analyze_report


def encode(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def save(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, default=encode, allow_nan=False)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("Dates must include a timezone.")
    return result.astimezone(timezone.utc)


def validate_plan(start, end, asset_id, provider, purpose, fee, slippage):
    if end <= start:
        raise ValueError("End must follow start.")
    seconds = (end - start).total_seconds()
    if seconds % 60 or seconds > 10000 * 60:
        raise ValueError("Use whole-minute intervals of at most 10,000 minutes.")
    if type(asset_id) is not int or asset_id < 1:
        raise ValueError("Asset ID must be positive.")
    if provider not in COLLECTED_PROVIDERS:
        raise ValueError("Unsupported collected provider.")
    if not purpose.strip():
        raise ValueError("State the purpose before running the evaluation.")
    for cost in (fee, slippage):
        if not cost.is_finite() or not 0 <= cost < 10000:
            raise ValueError("Costs must be finite and between 0 and 10,000 bps.")
    return int(seconds / 60)


def run(args):
    start, end = parse_time(args.start), parse_time(args.end)
    ticks = validate_plan(
        start, end, args.asset_id, args.provider,
        args.purpose, args.fee_bps, args.slippage_bps,
    )
    root = Path(__file__).resolve().parents[2]
    directory = root / "work/evaluations" / uuid4().hex
    directory.mkdir(parents=True)
    manifest = capture_replay_manifest()
    plan = {
        "schema_version": 1,
        "designation": "development",
        "purpose": args.purpose.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "asset_id": args.asset_id, "provider": args.provider,
        "start": start.isoformat(), "end_exclusive": end.isoformat(),
        "decision_ticks": ticks,
        "schedule": "one-minute ticks; regular every fifth tick from start",
        "fee_bps": args.fee_bps, "slippage_bps": args.slippage_bps,
        "policy": asdict(POLICY),
        "execution_manifest": manifest,
        "holdout_protected": False,
    }
    hashes = {"plan.json": save(directory / "plan.json", plan)}
    print("Evaluation directory:", directory, flush=True)

    try:
        windows = []
        last_price = None
        with SessionLocal() as session:
            session.execute(text(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            ))
            session.execute(text("SET LOCAL statement_timeout = '30s'"))
            asset = session.execute(
                select(MarketAsset.symbol, MarketAsset.asset_type)
                .where(MarketAsset.id == args.asset_id)
            ).one_or_none()
            if asset is None:
                raise ValueError("Asset ID does not exist.")
            expected_type = {"Finnhub": "stock", "CoinGecko": "crypto"}
            if asset.asset_type != expected_type[args.provider]:
                raise ValueError("Provider and asset type do not match.")

            simulations = {
                "zero_cost": PositionSimulation(
                    symbol=asset.symbol, policy=POLICY,
                    fee_bps=0, slippage_bps=0,
                ),
                "specified_costs": PositionSimulation(
                    symbol=asset.symbol, policy=POLICY,
                    fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
                ),
            }
            for index in range(ticks):
                at = start + timedelta(minutes=index)
                window = load_historical_snapshot(
                    session, asset_id=args.asset_id,
                    provider=args.provider, decision_at=at,
                )
                snapshot = window["snapshot"]
                risk_only = index % 5 != 0
                values = asdict(snapshot)
                values["observation_at"] = (
                    snapshot.observation_at.isoformat()
                    if snapshot.observation_at else None
                )
                windows.append({
                    "decision_at": at.isoformat(), "risk_only": risk_only,
                    "observation_ids": window["observation_ids"],
                    "snapshot": values,
                })
                for simulation in simulations.values():
                    simulation.step(
                        snapshot, decision_at=at, risk_only=risk_only
                    )
                if snapshot.latest_price_usd is not None:
                    last_price = snapshot.latest_price_usd

        if last_price is None:
            raise ValueError("No reference price available for final valuation.")
        verify_replay_manifest(manifest)
        report = {
            "mode": "single_asset_engineering_position_replay",
            "designation": "development",
            "asset_id": args.asset_id, "symbol": asset.symbol,
            "provider": args.provider,
            "start": start.isoformat(), "end_exclusive": end.isoformat(),
            "availability_verified": False,
            "policy": asdict(POLICY), "execution_manifest": manifest,
            "assumptions": [
                "Synthetic schedule, not actual recorded cycle times.",
                "Fills use stored pre-decision prices plus assumed slippage.",
                "Open positions are marked, not forcibly liquidated.",
                "No verified historical availability or live-price parity.",
            ],
            "windows": windows, "scenarios": {},
        }
        for name, simulation in simulations.items():
            ledger = simulation.ledger
            report["scenarios"][name] = {
                "fee_bps": ledger.fee_bps,
                "slippage_bps": ledger.slippage_bps,
                "account": ledger.mark(last_price),
                "recovery_target": simulation.target,
                "opened_at": (
                    simulation.opened_at.isoformat()
                    if simulation.opened_at else None
                ),
                "events": simulation.events,
            }
        hashes["report.json"] = save(directory / "report.json", report)

        # Verify exactly what was serialized, not the in-memory objects.
        saved = json.loads((directory / "report.json").read_text())
        verification = verify_report(saved)
        hashes["verification.json"] = save(
            directory / "verification.json", verification
        )
        analysis = analyze_report(saved)
        hashes["analysis.json"] = save(directory / "analysis.json", analysis)
        save(directory / "result.json", {
            "status": "completed",
            "designation": "development",
            "promotion_authorized": False,
            "artifacts_sha256": hashes,
        })
        for name, result in analysis["scenarios"].items():
            print(
                name, "trades=", result["completed_trades"],
                "equity=", result["ending_equity"],
                "return_percent=", result["return_percent"],
                "stale_ticks=", result["data_quality"].get(
                    "missing_or_stale_reference_ticks", 0
                ),
            )
        print("PASS: saved replay verified and analyzed.")
    except Exception as error:
        save(directory / "failure.json", {
            "status": "failed", "error_type": type(error).__name__,
            "message": str(error), "completed_artifacts_sha256": hashes,
        })
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-id", type=int, required=True)
    parser.add_argument("--provider", choices=sorted(COLLECTED_PROVIDERS), required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--purpose", required=True)
    parser.add_argument("--fee-bps", type=Decimal, required=True)
    parser.add_argument("--slippage-bps", type=Decimal, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
