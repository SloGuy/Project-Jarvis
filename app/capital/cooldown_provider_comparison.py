"""Read-only development comparison using a sealed provider collection."""

from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta

from app.capital import validation_registry as registry
from app.capital.cooldown_comparison import compare_loss_cooldown
from app.capital.cooldown_manifest import (
    _plain,
    verify_cooldown_manifest,
)
from app.capital.replay_manifest import verify_replay_manifest
from app.capital.validation_plan import timestamp
from app.capital.validation_provider_collection import (
    materialize_provider_collection,
    require_provider_plan,
)
from app.capital.validation_provider_verification import open_provider_packet


def authorize_read():
    from app.agents.capital_registry import RESEARCH_AGENT_ID
    from app.capital.autonomy_control import read_operating_policy
    from app.capital.autonomy_policy import authorize_capital_action

    authorize_capital_action(
        agent_id=RESEARCH_AGENT_ID,
        action="capital.inspect_evidence",
        policy=read_operating_policy(),
        execution_mode="paper",
    )


def run_provider_comparison(*, plan_id, policy, saved_manifest):
    """Compare accounts without claiming or modifying the original plan."""
    authorize_read()
    row = registry.get_plan(plan_id)
    if row["status"] != "completed":
        raise ValueError("Original validation must be completed first.")

    plan = require_provider_plan(row)
    if _plain(asdict(policy)) != _plain(plan["policy"]):
        raise ValueError("Comparison policy differs from the source plan.")

    verify_replay_manifest(plan["execution_manifest"])
    manifest = verify_cooldown_manifest(
        saved_manifest,
        policy=policy,
        fee_bps=plan["fee_bps"],
        slippage_bps=plan["slippage_bps"],
    )

    start = timestamp(plan["start"])
    end = timestamp(plan["end_exclusive"])
    seconds = (end - start).total_seconds()
    if seconds <= 0 or seconds % 60 or seconds > 10000 * 60:
        raise ValueError("Invalid comparison period.")

    materialized = materialize_provider_collection(row)
    evidence = {
        "schema_version": 1,
        "envelope": deepcopy(row["envelope"]),
        "registered_sha256": row["registered_sha256"],
        "collection": deepcopy(materialized["collection"]),
        "receipts": deepcopy(materialized["receipts"]),
    }

    # Reconstruct inputs from the verified embedded receipt records.
    with open_provider_packet(
        evidence,
        expected_sha256=row["registered_sha256"],
        expected_collection=row["provider_collection"],
    ) as history:
        inputs = []
        windows = []
        for index in range(int(seconds / 60)):
            at = start + timedelta(minutes=index)
            window = history.window(decision_at=at.isoformat())
            risk_only = index % 5 != 0
            snapshot = window["snapshot"]

            inputs.append({
                "snapshot": snapshot,
                "decision_at": at,
                "risk_only": risk_only,
            })
            values = asdict(snapshot)
            values["observation_at"] = (
                snapshot.observation_at.isoformat()
                if snapshot.observation_at is not None else None
            )
            windows.append({
                "decision_at": at.isoformat(),
                "risk_only": risk_only,
                "snapshot": values,
                "observation_ids": deepcopy(window["observation_ids"]),
                "provider_input": deepcopy(window["provider_input"]),
            })

    authorize_read()
    result = compare_loss_cooldown(
        symbol=plan["symbol"],
        policy=policy,
        fee_bps=plan["fee_bps"],
        slippage_bps=plan["slippage_bps"],
        decision_inputs=inputs,
    )

    # Reject configuration or retained-state changes during the read.
    verify_cooldown_manifest(
        manifest,
        policy=policy,
        fee_bps=plan["fee_bps"],
        slippage_bps=plan["slippage_bps"],
    )
    current = registry.get_plan(plan_id)
    if current != row:
        raise ValueError("Source validation state changed during comparison.")
    materialize_provider_collection(current)
    authorize_read()

    result.update({
        "source_validation_plan_id": plan_id,
        "source_validation_sha256": row["registered_sha256"],
        "comparison_manifest": manifest,
        "provider_evidence": evidence,
        "windows": windows,
        "collection_chain_verified": True,
        "retained_checkpoint_verified": True,
        "comparison_preregistered": False,
        "comparison_packet_verified": False,
        "validation_ready": False,
        "registry_writes": False,
        "database_writes": False,
    })
    result["limitations"].append(
        "The source validation registration did not register this comparison."
    )
    return result
