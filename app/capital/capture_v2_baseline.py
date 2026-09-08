"""Capture current V2 disk configuration; never represents original launch."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]

FILES = [
    "app/autonomous_trading/mean_reversion_v2_strategy.py",
    "app/autonomous_trading/mean_reversion_v2_exit.py",
    "app/autonomous_trading/policy.py",
    "app/autonomous_trading/signal_confirmation.py",
    "app/autonomous_trading/exit_rules.py",
    "app/autonomous_trading/trade_journal.py",
    "app/autonomous_trading/strategy.py",
    "app/capital/mean_reversion_v2_paper_runner.py",
    "app/capital/mean_reversion_v2_context.py",
    "app/capital/mean_reversion_runner.py",
    "app/capital/run_mean_reversion_v2_cycle.py",
    "app/capital/candidate_pipeline.py",
    "app/capital/policies.py",
    "app/capital/experiment_registry.py",
    "app/capital/strategy_registry.py",
    "app/market_db/history.py",
    "app/market_universe.py",
    "app/capital/capture_v2_baseline.py",
]


def encode(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Unsupported value: {type(value).__name__}")


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True,
        allow_nan=False, default=encode,
    ).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def command(*args):
    result = subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, timeout=20
    )
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def sources():
    result = {}
    for name in FILES:
        raw = (ROOT / name).read_bytes()
        result[name] = {
            "sha256": digest(raw),
            "source": raw.decode("utf-8"),
        }
    return result


def verify(path):
    envelope = json.loads(path.read_text())
    payload = envelope["payload"]
    if digest(canonical(payload)) != envelope["sha256"]:
        raise ValueError("Manifest checksum mismatch.")
    for name, item in payload.get("sources", {}).items():
        if digest(item["source"].encode("utf-8")) != item["sha256"]:
            raise ValueError(f"Source checksum mismatch: {name}")
    return payload


def write_manifest(directory, payload):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"v2_baseline_{uuid4().hex}.json"
    envelope = {"payload": payload, "sha256": digest(canonical(payload))}
    with path.open("x", encoding="utf-8") as handle:
        json.dump(envelope, handle, indent=2, default=encode, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    verify(path)
    return path


def capture():
    started = datetime.now(timezone.utc).isoformat()
    before = sources()
    head = command("git", "rev-parse", "HEAD")

    from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY
    from app.autonomous_trading import mean_reversion_v2_strategy as strategy
    from app.autonomous_trading.mean_reversion_v2_exit import MAX_HOLDING_TIME
    from app.autonomous_trading.signal_confirmation import REQUIRED_CONFIRMATIONS

    settings = {
        name: getattr(strategy, name)
        for name in (
            "LOOKBACK_OBSERVATIONS", "MINIMUM_OBSERVATIONS",
            "ENTRY_Z_SCORE", "RECOVERY_Z_SCORE",
            "DEFAULT_POSITION_PERCENT", "BASE_CONFIDENCE_PERCENT",
            "MAX_CONFIDENCE_PERCENT",
        )
    }
    settings["required_confirmations"] = REQUIRED_CONFIRMATIONS
    settings["maximum_holding_seconds"] = MAX_HOLDING_TIME.total_seconds()

    units = {}
    for stem in (
        "jarvis-mean-reversion-v2",
        "jarvis-mean-reversion-v2-risk",
        "jarvis-market-collector",
    ):
        for suffix, properties in (
            ("timer", [
                "TimersMonotonic", "TimersCalendar", "AccuracyUSec",
                "RandomizedDelayUSec", "Persistent",
            ]),
            ("service", ["User", "WorkingDirectory", "ExecStart"]),
        ):
            unit = f"{stem}.{suffix}"
            units[unit] = command(
                "systemctl", "show", "--no-pager", "--full", unit,
                "-p", ",".join(["Id", "LoadState"] + properties),
            )
            if "LoadState=loaded" not in units[unit]:
                raise RuntimeError(f"Unit unavailable: {unit}")

    if before != sources() or head != command("git", "rev-parse", "HEAD"):
        raise RuntimeError("Sources changed during capture; run again.")

    payload = {
        "schema_version": 1,
        "kind": "current_configuration_baseline",
        "capture_started_at": started,
        "capture_completed_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": "mean_reversion_v2_paper_2026",
        "original_launch_verified": False,
        "running_process_configuration_verified": False,
        "git_head": head,
        "selected_source_status": command(
            "git", "status", "--porcelain", "--", *FILES
        ),
        "strategy_settings": settings,
        "risk_policy": asdict(MEAN_REVERSION_V2_1000_POLICY),
        "systemd": units,
        "sources": before,
        "limitations": [
            "Current disk configuration, not original launch configuration.",
            "Selected source files, not a complete dependency archive.",
            "Actual universe membership and open positions not captured.",
            "Market data and signal-confirmation state not captured.",
            "External packages and environment variables not captured.",
            "No trading-cycle execution or historical validation performed.",
            "Systemd properties were read sequentially, not atomically.",
        ],
        "audit_findings": [
            "Lookback counts observations rather than fixed-duration bars.",
            "Recovery target is recomputed from saved entry statistics.",
            "Fixed recovery and timeout exits run in regular cycles only.",
            "Journal thesis evaluator has no mean_reversion_v2 branch.",
        ],
    }
    path = write_manifest(ROOT / "runtime/capital/baselines", payload)
    print(f"BASELINE SAVED: {path}")
    print(f"Source files: {len(before)}")
    print(f"Git revision: {head}")
    print("Integrity verification: PASS")
    print("Original launch verified: NO")
    print("Trading cycles executed: NONE")


def self_test():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        payload = {
            "kind": "test",
            "sources": {"sample.py": {
                "source": "x = 1\n",
                "sha256": digest(b"x = 1\n"),
            }},
        }
        first = write_manifest(root, payload)
        original = first.read_bytes()
        second = write_manifest(root, payload)
        assert first != second and first.read_bytes() == original
        assert verify(first) == payload
        envelope = json.loads(first.read_text())
        envelope["payload"]["kind"] = "changed"
        first.write_text(json.dumps(envelope))
        try:
            verify(first)
        except ValueError:
            pass
        else:
            raise AssertionError("Changed manifest was accepted")
    print("PASS: round trip, separate captures, and tamper detection")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
    elif args.verify:
        verify(args.verify)
        print("PASS: manifest and included source checksums")
    else:
        capture()
