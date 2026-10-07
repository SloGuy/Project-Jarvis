"""Execute a registered prospective validation plan exactly once."""

import argparse
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

from app.capital import validation_registry as registry
from app.capital.validation_plan import check_binding, verify_plan


def current_binding(plan):
    from app.capital.research_service import require_research_candidate
    from app.capital.strategy_registry import require_strategy
    from app.capital.replay_manifest import capture_replay_manifest
    from app.capital.run_evaluation import encode, POLICY

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
        plan,
        candidate,
        strategy.version,
        policy,
        capture_replay_manifest(),
    )


def run(args, *, validation_record):
    from app.capital.run_evaluation import run as evaluate

    return evaluate(args, validation_record=validation_record)


def materialize_provider_evidence(row):
    from app.capital.validation_provider_collection import (
        materialize_provider_collection,
    )

    return materialize_provider_collection(row)


def verify_registered_evidence(
    plan,
    report,
    registered_row,
    expected_registration,
    *,
    expected_status="running",
):
    from app.capital.validation_provider_verification import same

    retained_plan = verify_plan(
        registered_row["envelope"],
        expected_sha256=registered_row["registered_sha256"],
    )

    if (
        registered_row["plan_id"] != expected_registration["plan_id"]
        or registered_row["registered_sha256"]
        != expected_registration["sha256"]
        or registered_row["status"] != expected_status
        or not same(plan, retained_plan)
        or not same(
            report.get("validation_registration"),
            expected_registration,
        )
    ):
        raise ValueError("Assessment differs from the claimed registration.")

    if plan["schema_version"] == 2:
        from app.capital.validation_provider_verification import (
            open_provider_packet,
            verify_provider_inputs,
        )

        retained = materialize_provider_evidence(registered_row)
        expected_packet = {
            "schema_version": 1,
            "envelope": registered_row["envelope"],
            "registered_sha256": registered_row["registered_sha256"],
            "collection": retained["collection"],
            "receipts": retained["receipts"],
        }
        packet = report.get("provider_evidence")

        if not same(packet, expected_packet):
            raise ValueError(
                "Report differs from registered provider evidence."
            )

        with open_provider_packet(
            packet,
            expected_sha256=registered_row["registered_sha256"],
            expected_collection=registered_row["provider_collection"],
        ):
            pass

        verify_provider_inputs(report)
        return

    if (
        "provider_collection" in registered_row
        or "provider_evidence" in report
    ):
        raise ValueError("Legacy plans cannot use provider collections.")

    expected_collection = registered_row.get("witness_collection")

    if expected_collection is not None:
        from app.capital.validation_collection import (
            materialize_collection,
            validate_collection,
        )

        validate_collection(
            expected_collection,
            registered_row["registered_sha256"],
        )
        expected_collection = materialize_collection(expected_collection)

        if (
            expected_collection["status"] != "sealed"
            or not same(
                report.get("witness_collection"),
                expected_collection,
            )
            or not same(
                report.get("witness_evidence", {}).get("receipts"),
                expected_collection["receipts"],
            )
        ):
            raise ValueError(
                "Report differs from registered witness collection."
            )
    elif (
        report.get("availability_verified") is True
        or report.get("witness_collection") is not None
        or report.get("witness_evidence") is not None
    ):
        raise ValueError(
            "Witness evidence was not bound to this registration."
        )


def persist_assessment(plan, directory, expected_registration):
    from app.capital.validation_assessment import assess_report

    names = (
        "plan.json",
        "report.json",
        "verification.json",
        "analysis.json",
    )
    result_raw = (directory / "result.json").read_bytes()
    result = json.loads(result_raw)

    if (
        result.get("status") != "completed"
        or result.get("designation") != "prospective_validation"
        or result.get("validation_registration") != expected_registration
        or result.get("promotion_authorized") is not False
    ):
        raise ValueError("Replay result does not match the registered run.")

    raw = {
        name: (directory / name).read_bytes()
        for name in names
    }
    hashes = {
        name: hashlib.sha256(value).hexdigest()
        for name, value in raw.items()
    }

    if result.get("artifacts_sha256") != hashes:
        raise ValueError("Replay packet artifact hashes do not match.")

    report = json.loads(raw["report.json"])
    registered_row = registry.get_plan(
        expected_registration["plan_id"]
    )

    verify_registered_evidence(
        plan,
        report,
        registered_row,
        expected_registration,
    )

    # Assessment recomputes verification and analysis from the report.
    assessment = assess_report(plan, report)
    assessment.update({
        "input_report_sha256": hashes["report.json"],
        "input_result_sha256": hashlib.sha256(result_raw).hexdigest(),
    })

    path = directory / "assessment.json"

    with path.open("x", encoding="utf-8") as handle:
        json.dump(
            assessment,
            handle,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        handle.flush()
        os.fsync(handle.fileno())

    return {
        "assessment_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "criteria_status": assessment["criteria_status"],
        "validation_status": assessment["validation_status"],
    }


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
            raise ValueError(
                "Completed packet does not match the registration."
            )

        assessed = persist_assessment(plan, directory, expected)

        receipt = {
            **assessed,
            "directory": str(directory.resolve()),
            "result_sha256": hashlib.sha256(
                result_path.read_bytes()
            ).hexdigest(),
            "acceptance_assessed": True,
            "promotion_authorized": False,
        }

        registry.finish_plan(
            plan_id,
            running["run_token"],
            succeeded=True,
            detail=json.dumps(receipt, sort_keys=True),
        )

    except Exception as error:
        registry.finish_plan(
            plan_id,
            running["run_token"],
            succeeded=False,
            detail=f"{type(error).__name__}: {error}",
        )
        raise

    print("REGISTERED RUN COMPLETED:", directory)
    print("Validation assessment:", assessed["validation_status"])
    print("Promotion is not authorized.")
    return directory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("plan_id")
    args = parser.parse_args()
    execute_registered(args.plan_id)


if __name__ == "__main__":
    main()
