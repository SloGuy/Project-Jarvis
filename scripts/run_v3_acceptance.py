"""Explicit isolated V3 acceptance suite; no service or trading commands."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "test_observation_witness",
    "test_witnessed_history",
    "test_witness_verification",
    "test_validation_collection",
    "test_witness_execution",
    "test_paper_comparison",
    "test_scanner_continuity",
    "test_candidate_pipeline",
    "test_mean_reversion_math",
    "test_historical_observations",
    "test_simulated_ledger",
    "test_position_simulation",
    "test_offline_verification",
    "test_replay_manifest",
    "test_replay_analysis",
    "test_evaluation_plan",
    "test_research_evidence",
    "test_research_workflow",
    "test_validation_plan",
    "test_validation_registry",
    "test_validation_access",
    "test_registered_validation_runner",
    "test_validation_integration",
    "test_validation_assessment",
    "test_committee_validation",
    "test_committee_lineage_gate",
    "test_capital_committee",
    "test_shared_shadow_ledger",
    "test_shadow_multiasset",
    "test_breakout_replay",
    "test_volatility_breakout_strategy",
    "test_shadow_coordinator",
    "test_shadow_allocation_sizing",
    "test_shadow_checkpoint",
    "test_shadow_inputs",
    "test_shared_replay",
    "test_shadow_risk",
    "test_capital_allocator",
    "test_allocation_policy",
)

def main():
    for name in TESTS:
        if not (ROOT / f"tests/{name}.py").exists():
            raise FileNotFoundError(f"Required acceptance module is missing: {name}")
    directory = ROOT / "work/acceptance" / uuid4().hex
    directory.mkdir(parents=True)
    results = []
    for name in TESTS:
        program = f"""
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import runpy
from app.capital import research_store, validation_registry
with TemporaryDirectory() as tmp, ExitStack() as stack:
    root = Path(tmp)
    stack.enter_context(patch.object(validation_registry, "DIRECTORY", root / "validation"))
    stack.enter_context(patch.object(research_store, "STATE_DIRECTORY", root / "research"))
    stack.enter_context(patch.object(research_store, "RESEARCH_STATE_FILE", root / "research/state.json"))
    stack.enter_context(patch.object(research_store, "RESEARCH_LOCK_FILE", root / "research/state.lock"))
    if {name!r} == "test_capital_committee":
        stack.enter_context(patch(
            "app.capital.graduation_gates.get_capital_safety_audit",
            return_value={{"status": "passed"}},
        ))
    runpy.run_path("tests/{name}.py", run_name="__main__")
"""
        result = subprocess.run(
            [sys.executable, "-c", program], cwd=ROOT,
            capture_output=True, text=True,
        )
        (directory / f"{name}.log").write_text(
            result.stdout + result.stderr, encoding="utf-8"
        )
        results.append({"test_module": name, "exit_code": result.returncode})
        print("PASS" if result.returncode == 0 else "FAIL", name, flush=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    record = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": revision,
        "working_tree_status": status,
        "status": "passed" if all(r["exit_code"] == 0 for r in results) else "failed",
        "test_modules": results,
        "scope": "Isolated V3 engineering acceptance; not a live operational audit.",
        "limitations": [
            "Committee's operational safety dependency is synthetic in its legacy unit test.",
            "Prospective market-data availability and strategy effectiveness are not established.",
            "No service restart, paper trading cycle, or live execution is performed.",
        ],
    }
    (directory / "result.json").write_text(json.dumps(record, indent=2))
    print("Acceptance record:", directory / "result.json")
    if record["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
