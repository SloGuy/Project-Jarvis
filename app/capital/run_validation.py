"""Execute a registered prospective validation plan exactly once."""
import argparse
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from app.capital import validation_registry as registry
from app.capital.validation_plan import check_binding, verify_plan
from app.capital.research_service import require_research_candidate
from app.capital.strategy_registry import require_strategy
from app.capital.replay_manifest import capture_replay_manifest
from app.capital.run_evaluation import run, encode, POLICY


def current_binding(plan):
    candidate = require_research_candidate(
        research_id=plan["research"]["research_id"]
    )
    strategy = require_strategy(strategy_name=candidate.strategy_name)
    if (
        strategy.name != "mean_reversion_v2"
        or strategy.implementation_module
        != "app.autonomous_trading.mean_reversion_v2_strategy"
        or strategy.evaluator_name != "evaluate_mean_reversion_v2_strategy"
    ):
        raise ValueError("This runner supports Mean Reversion V2 only.")
    policy = json.loads(json.dumps(asdict(POLICY), default=encode))
    check_binding(
        plan, candidate, strategy.version, policy, capture_replay_manifest()
    )


def execute_registered(plan_id):
    registered = registry.get_plan(plan_id)
    plan = verify_plan(
        registered["envelope"],
        expected_sha256=registered["registered_sha256"],
    )
    # Binding errors before claiming do not consume the plan.
    current_binding(plan)
    running = registry.claim_plan(plan_id)
    try:
        # Recheck after claiming, then again after execution.
        current_binding(plan)
        args = SimpleNamespace(
            asset_id=plan["asset_id"],
            provider=plan["provider"],
            start=plan["start"],
            end=plan["end_exclusive"],
            purpose=f"Registered validation {plan_id}",
            fee_bps=Decimal(plan["fee_bps"]),
            slippage_bps=Decimal(plan["slippage_bps"]),
        )
        directory = Path(run(args, validation_record=running))
        current_binding(plan)
        result_path = directory / "result.json"
        result = json.loads(result_path.read_text())
        expected = {
            "plan_id": plan_id,
            "sha256": registered["registered_sha256"],
        }
        if (
            result.get("status") != "completed"
            or result.get("designation") != "prospective_validation"
            or result.get("validation_registration") != expected
            or result.get("promotion_authorized") is not False
        ):
            raise ValueError("Completed packet does not match the registration.")
        receipt = {
            "directory": str(directory.resolve()),
            "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
            "acceptance_assessed": False,
            "promotion_authorized": False,
        }
        registry.finish_plan(
            plan_id, running["run_token"], succeeded=True,
            detail=json.dumps(receipt, sort_keys=True),
        )
    except Exception as error:
        registry.finish_plan(
            plan_id, running["run_token"], succeeded=False,
            detail=f"{type(error).__name__}: {error}",
        )
        raise
    print("REGISTERED RUN COMPLETED:", directory)
    print("Acceptance assessment remains pending; promotion is not authorized.")
    return directory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("plan_id")
    args = parser.parse_args()
    execute_registered(args.plan_id)


if __name__ == "__main__":
    main()
