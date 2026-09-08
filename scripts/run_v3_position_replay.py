import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from app.market_db.database import SessionLocal
from app.capital.historical_observations import load_historical_snapshot
from app.capital.position_simulation import PositionSimulation
from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY as POLICY


def encode(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


start = datetime(2026, 9, 2, 14, tzinfo=timezone.utc)
end = datetime(2026, 9, 5, 14, tzinfo=timezone.utc)

scenarios = {
    "zero_cost": PositionSimulation(
        symbol="SPY", policy=POLICY, fee_bps=0, slippage_bps=0
    ),
    "illustrative_costs": PositionSimulation(
        symbol="SPY", policy=POLICY, fee_bps=5, slippage_bps=5
    ),
}
from app.capital.replay_manifest import (
    capture_replay_manifest, verify_replay_manifest,
)
execution_manifest = capture_replay_manifest()
windows = []
last_price = None

with SessionLocal() as session:
    session.execute(text(
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    ))
    session.execute(text("SET LOCAL statement_timeout = '30s'"))
    at = start
    while at < end:
        window = load_historical_snapshot(
            session, asset_id=1, provider="Finnhub", decision_at=at
        )
        snapshot = window["snapshot"]
        risk_only = int((at - start).total_seconds()) % 300 != 0
        windows.append({
            "decision_at": at.isoformat(),
            "risk_only": risk_only,
            "observation_ids": window["observation_ids"],
            "snapshot": {
                **asdict(snapshot),
                "observation_at": (
                    snapshot.observation_at.isoformat()
                    if snapshot.observation_at else None
                ),
            },
        })
        for simulation in scenarios.values():
            simulation.step(
                snapshot, decision_at=at, risk_only=risk_only
            )
        if snapshot.latest_price_usd is not None:
            last_price = snapshot.latest_price_usd
        at += timedelta(minutes=1)

if last_price is None:
    raise RuntimeError("No final reference price available.")

verify_replay_manifest(execution_manifest)

report = {
    "execution_manifest": execution_manifest,
    "mode": "single_asset_engineering_position_replay",
    "start": start.isoformat(),
    "end_exclusive": end.isoformat(),
    "availability_verified": False,
    "policy": asdict(POLICY),
    "assumptions": [
        "Synthetic five-minute regular and one-minute risk schedule.",
        "Coincident regular/risk ticks evaluated once as a regular tick.",
        "Fills use latest pre-decision stored price plus assumed slippage.",
        "Live quote history, order latency and partial fills not modeled.",
        "Open positions remain open; no forced end-of-window liquidation.",
        "Final equity uses last available reference mark, not exit value.",
    ],
    "windows": windows,
    "scenarios": {},
}

for name, simulation in scenarios.items():
    ledger = simulation.ledger
    account = ledger.mark(last_price)
    fills = ledger.fills
    result = {
        "fee_bps": ledger.fee_bps,
        "slippage_bps": ledger.slippage_bps,
        "account": account,
        "recovery_target": simulation.target,
        "opened_at": (
            simulation.opened_at.isoformat()
            if simulation.opened_at else None
        ),
        "events": simulation.events,
    }
    report["scenarios"][name] = result
    print(
        name,
        "buys=", sum(f["side"] == "buy" for f in fills),
        "sells=", sum(f["side"] == "sell" for f in fills),
        "equity=", account["equity"],
        "realized_pnl=", account["realized_pnl"],
        "unrealized_pnl=", account["unrealized_pnl"],
        "fees=", account["total_fees"],
        "open_quantity=", account["quantity"],
    )

output = ROOT / "work" / f"spy_position_replay_{uuid4().hex}.json"
with output.open("x") as handle:
    json.dump(report, handle, indent=2, default=encode, allow_nan=False)

print("Report:", output)
print("Decision ticks:", len(windows))
print("Database writes: NONE")
