"""Atomic shadow checkpoints rebuilt from recorded inputs. No trading I/O."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import get_args, get_type_hints

from app.capital.shadow_coordinator import ShadowCoordinator
from app.capital.offline_verification import restore_policy
from app.capital.replay_manifest import capture_replay_manifest
from app.capital.mean_reversion_math import MeanReversionSnapshot
from app.autonomous_trading.volatility_breakout_strategy import VolatilityBreakoutSnapshot

ROOT = Path(__file__).resolve().parents[2]
CLASSES = {
    "mean_reversion_v2": MeanReversionSnapshot,
    "volatility_breakout_v1": VolatilityBreakoutSnapshot,
}
EXTRA_SOURCES = (
    "app/capital/shadow_checkpoint.py",
    "app/capital/shadow_coordinator.py",
    "app/capital/shadow_risk.py",
    "app/capital/shared_shadow_ledger.py",
    "app/autonomous_trading/volatility_breakout_strategy.py",
)


def normalized(value):
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


def fingerprint():
    return {
        "core": capture_replay_manifest(),
        "shadow_sources": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in EXTRA_SOURCES
        },
    }


@contextmanager
def locked(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "shadow.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield directory / "shadow.json"
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_checkpoint(path, state):
    envelope = {"state": state, "sha256": digest(state)}
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix="shadow_")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(envelope, handle, sort_keys=True, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_checkpoint(path):
    envelope = json.loads(path.read_text())
    state = envelope["state"]
    if envelope.get("sha256") != digest(state):
        raise ValueError("Shadow checkpoint integrity mismatch.")
    if state.get("schema_version") != 1 or state["fingerprint"] != fingerprint():
        raise ValueError("Shadow checkpoint configuration changed.")
    return state


def encode_tick(*, decision_at, snapshots, quotes, risk_only=False,
                risk_mode="normal", size_scales=None):
    return normalized({
        "decision_at": decision_at.isoformat(),
        "risk_only": risk_only,
        "risk_mode": risk_mode, "size_scales": size_scales or {},
        "snapshots": [
            {"strategy": strategy, "symbol": symbol, "snapshot": asdict(snapshot)}
            for (strategy, symbol), snapshot in sorted(snapshots.items())
        ],
        "quotes": {
            symbol: [price, observed.isoformat()]
            for symbol, (price, observed) in sorted(quotes.items())
        },
    })


def decode_tick(value):
    snapshots = {}
    for item in value["snapshots"]:
        cls = CLASSES[item["strategy"]]
        data = dict(item["snapshot"])
        for field, annotation in get_type_hints(cls).items():
            if data.get(field) is None:
                continue
            types = (annotation, *get_args(annotation))
            if Decimal in types:
                data[field] = Decimal(data[field])
            elif datetime in types:
                data[field] = datetime.fromisoformat(data[field])
        key = (item["strategy"], item["symbol"])
        if key in snapshots:
            raise ValueError("Duplicate checkpoint snapshot.")
        snapshots[key] = cls(**data)
    return {
        "decision_at": datetime.fromisoformat(value["decision_at"]),
        "risk_only": value["risk_only"],
        "risk_mode": value.get("risk_mode", "normal"),
        "size_scales": value.get("size_scales", {}),
        "snapshots": snapshots,
        "quotes": {
            symbol: (Decimal(price), datetime.fromisoformat(observed))
            for symbol, (price, observed) in value["quotes"].items()
        },
    }


def rebuild(state):
    config = state["configuration"]
    coordinator = ShadowCoordinator(
        policy=restore_policy(config["policy"]),
        strategy_caps=config["strategy_caps"],
        fee_bps=config["fee_bps"], slippage_bps=config["slippage_bps"],
    )
    for index, record in enumerate(state["ticks"]):
        result = normalized(coordinator.step(**decode_tick(record["input"])))
        if result != record["result"]:
            raise ValueError(f"Shadow replay mismatch at tick {index}.")
    return coordinator


def create_checkpoint(directory, *, policy, strategy_caps, fee_bps, slippage_bps):
    state = {
        "schema_version": 1,
        "fingerprint": fingerprint(),
        "configuration": normalized({
            "policy": asdict(policy), "strategy_caps": strategy_caps,
            "fee_bps": fee_bps, "slippage_bps": slippage_bps,
        }),
        "ticks": [],
    }
    rebuild(state)  # Validate configuration before writing.
    with locked(directory) as path:
        if path.exists():
            raise FileExistsError("Shadow checkpoint already exists.")
        write_checkpoint(path, state)


def process_tick(directory, **tick):
    with locked(directory) as path:
        state = read_checkpoint(path)
        coordinator = rebuild(state)
        result = normalized(coordinator.step(**tick))
        encoded = encode_tick(**tick)
        if state["ticks"] and state["ticks"][-1]["input"] == encoded:
            return result
        if len(state["ticks"]) >= 10000:
            raise ValueError("Shadow checkpoint reached its 10,000-tick bound.")
        state["ticks"].append({"input": encoded, "result": result})
        write_checkpoint(path, state)
        return result


def verify_checkpoint(directory):
    with locked(directory) as path:
        state = read_checkpoint(path)
        coordinator = rebuild(state)
        return {
            "status": "matched",
            "ticks": len(state["ticks"]),
            "last_result": normalized(coordinator.last_result),
            "paper_execution_authority": False,
            "live_capital_authority": False,
        }
