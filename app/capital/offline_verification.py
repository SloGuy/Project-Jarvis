"""Verify legacy position replay reports from saved snapshots."""
import argparse
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.engine import Engine

from app.capital.mean_reversion_math import MeanReversionSnapshot
from app.capital.position_simulation import PositionSimulation
from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY


def normalized(value):
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("Timestamp must include timezone.")
    return result


def restore_policy(values):
    template = MEAN_REVERSION_V2_1000_POLICY
    defaults = asdict(template)
    if set(values) != set(defaults):
        raise ValueError("Saved policy fields do not match the policy schema.")
    restored = {}
    for key, reference in defaults.items():
        value = values[key]
        if isinstance(reference, Decimal):
            value = Decimal(str(value))
            if not value.is_finite():
                raise ValueError(f"Nonfinite policy value: {key}")
        elif type(value) is not type(reference):
            raise ValueError(f"Invalid saved policy type: {key}")
        restored[key] = value
    return type(template)(**restored)


def restore_snapshot(values):
    data = dict(values)
    for name in (
        "latest_price_usd", "mean_price_usd",
        "standard_deviation_usd", "z_score",
    ):
        if data[name] is not None:
            data[name] = Decimal(str(data[name]))
            if not data[name].is_finite():
                raise ValueError(f"Nonfinite snapshot value: {name}")
    if data["observation_at"] is not None:
        data["observation_at"] = timestamp(data["observation_at"])
    if type(data["usable"]) is not bool:
        raise ValueError("Snapshot usable flag must be boolean.")
    if type(data["observation_count"]) is not int:
        raise ValueError("Observation count must be an integer.")
    return MeanReversionSnapshot(**data)


def require_equal(actual, expected, label):
    if normalized(actual) != expected:
        raise ValueError(f"Replay mismatch: {label}")


def verify_report(report):
    from app.capital.replay_manifest import verify_replay_manifest
    if "execution_manifest" in report:
        verify_replay_manifest(report["execution_manifest"])
    if report["mode"] != "single_asset_engineering_position_replay":
        raise ValueError("Unsupported report mode.")
    start = timestamp(report["start"])
    end = timestamp(report["end_exclusive"])
    windows = report["windows"]
    scenarios = report["scenarios"]
    if end <= start or not windows or not scenarios:
        raise ValueError("Empty or invalid replay interval.")
    if end != start + timedelta(minutes=len(windows)):
        raise ValueError("Missing or extra decision ticks.")
    if len(windows) > 10000:
        raise ValueError("Verification limited to 10,000 ticks.")

    policy = restore_policy(report["policy"])
    symbol = windows[0]["snapshot"]["symbol"]
    simulations = {
        name: PositionSimulation(
            symbol=symbol, policy=policy,
            fee_bps=expected["fee_bps"],
            slippage_bps=expected["slippage_bps"],
        )
        for name, expected in scenarios.items()
    }
    for name, expected in scenarios.items():
        if len(expected["events"]) != len(windows):
            raise ValueError(f"Event count mismatch: {name}")

    last_price = None
    # Imports may initialize SQLAlchemy objects; actual connections are blocked.
    with ExitStack() as stack:
        for attribute in ("connect", "raw_connection"):
            stack.enter_context(patch.object(
                Engine, attribute,
                side_effect=AssertionError("Database access during offline replay"),
            ))
        stack.enter_context(patch(
            "app.autonomous_trading.mean_reversion_v2_strategy."
            "update_signal_confirmation",
            side_effect=AssertionError("Live confirmation during offline replay"),
        ))
        from app.capital.witness_verification import verify_witness_inputs
        verify_witness_inputs(report)
        for index, window in enumerate(windows):
            at = timestamp(window["decision_at"])
            if at != start + timedelta(minutes=index):
                raise ValueError(f"Decision sequence mismatch at tick {index}.")
            if type(window["risk_only"]) is not bool:
                raise ValueError("risk_only must be boolean.")
            if window["risk_only"] != (index % 5 != 0):
                raise ValueError(f"Schedule mismatch at tick {index}.")
            snapshot = restore_snapshot(window["snapshot"])
            for name, simulation in simulations.items():
                event = simulation.step(
                    snapshot, decision_at=at,
                    risk_only=window["risk_only"],
                )
                require_equal(
                    event, scenarios[name]["events"][index],
                    f"{name}, event {index}, {at.isoformat()}",
                )
            if snapshot.latest_price_usd is not None:
                last_price = snapshot.latest_price_usd

    if last_price is None:
        raise ValueError("No final reference price.")
    results = {}
    for name, simulation in simulations.items():
        actual = {
            "fee_bps": simulation.ledger.fee_bps,
            "slippage_bps": simulation.ledger.slippage_bps,
            "account": simulation.ledger.mark(last_price),
            "recovery_target": simulation.target,
            "opened_at": (
                simulation.opened_at.isoformat()
                if simulation.opened_at else None
            ),
            "events": simulation.events,
        }
        require_equal(actual, scenarios[name], f"{name}, complete scenario")
        results[name] = {
            "matched_events": len(simulation.events),
            "fills": len(simulation.ledger.fills),
            "ending_equity": str(actual["account"]["equity"]),
        }
    return {"status": "matched", "ticks": len(windows), "scenarios": results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    raw = args.report.read_bytes()
    result = verify_report(json.loads(raw))
    root = Path(__file__).resolve().parents[2]
    hashes = {}
    for name, module in list(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if name.startswith("app.") and filename:
            path = Path(filename).resolve()
            if path.suffix == ".py" and path.is_relative_to(root):
                hashes[str(path.relative_to(root))] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    result.update({
        "report_sha256": hashlib.sha256(raw).hexdigest(),
        "verifier_source_hashes": hashes,
        "python_version": sys.version,
        "scope": "Saved snapshots through decisions, fills and final account.",
        "original_run_source_verified": False,
        "historical_availability_verified": False,
        "limitations": [
            "Original report did not pin all source and dependency versions.",
            "Snapshot arithmetic is not recomputed from raw market rows.",
            "Hashes record current disk sources, not original execution sources.",
        ],
    })
    output = root / "work" / f"offline_verification_{uuid4().hex}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps(result["scenarios"], indent=2))
    print("MATCHED: every saved event and final scenario")
    print("Decision ticks:", result["ticks"])
    print("Verification record:", output)
    print("Database connections during replay: BLOCKED")


if __name__ == "__main__":
    main()
