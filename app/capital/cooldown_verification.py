"""Reconstruct a development cooldown comparison from embedded receipts.

Expected references must come from retained records, not the report itself.
Verification establishes reproducibility, not profitability or promotion.
"""

from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
import json

from app.capital.cooldown_comparison import compare_loss_cooldown
from app.capital.cooldown_manifest import _plain, verify_cooldown_manifest
from app.capital.replay_manifest import verify_replay_manifest
from app.capital.validation_plan import timestamp
from app.capital.validation_provider_verification import open_provider_packet


def canonical(value):
    return json.dumps(
        _plain(value),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def verify_cooldown_packet(
    report,
    *,
    policy,
    expected_manifest,
    expected_plan_id,
    expected_sha256,
    expected_collection,
):
    """Verify against independently retained configuration and collection."""
    if not isinstance(report, dict):
        raise ValueError("Comparison report must be a dictionary.")
    if (
        not isinstance(expected_plan_id, str)
        or not expected_plan_id.strip()
    ):
        raise ValueError("Expected source plan ID is required.")
    if not isinstance(expected_collection, dict):
        raise ValueError("Retained collection is required.")
    if (
        report.get("source_validation_plan_id") != expected_plan_id
        or report.get("source_validation_sha256") != expected_sha256
    ):
        raise ValueError("Comparison source registration differs.")
    if canonical(report.get("comparison_manifest")) != canonical(
        expected_manifest
    ):
        raise ValueError("Comparison manifest differs from retained reference.")

    evidence = report.get("provider_evidence")
    with open_provider_packet(
        evidence,
        expected_sha256=expected_sha256,
        expected_collection=expected_collection,
    ) as history:
        plan = history.plan
        if canonical(asdict(policy)) != canonical(plan["policy"]):
            raise ValueError("Comparison policy differs from source plan.")

        verify_replay_manifest(plan["execution_manifest"])
        manifest = verify_cooldown_manifest(
            expected_manifest,
            policy=policy,
            fee_bps=plan["fee_bps"],
            slippage_bps=plan["slippage_bps"],
        )

        start = timestamp(plan["start"])
        end = timestamp(plan["end_exclusive"])
        seconds = (end - start).total_seconds()
        if seconds <= 0 or seconds % 60 or seconds > 10000 * 60:
            raise ValueError("Invalid comparison period.")
        ticks = int(seconds / 60)

        saved_windows = report.get("windows")
        if not isinstance(saved_windows, list) or len(saved_windows) != ticks:
            raise ValueError("Comparison decision-window count differs.")

        inputs = []
        windows = []
        for index in range(ticks):
            at = start + timedelta(minutes=index)
            window = history.window(decision_at=at.isoformat())
            snapshot = window["snapshot"]
            risk_only = index % 5 != 0
            values = asdict(snapshot)
            values["observation_at"] = (
                snapshot.observation_at.isoformat()
                if snapshot.observation_at is not None else None
            )
            rebuilt = {
                "decision_at": at.isoformat(),
                "risk_only": risk_only,
                "snapshot": values,
                "observation_ids": deepcopy(window["observation_ids"]),
                "provider_input": deepcopy(window["provider_input"]),
            }
            if canonical(saved_windows[index]) != canonical(rebuilt):
                raise ValueError(
                    f"Comparison input mismatch at tick {index}."
                )
            windows.append(rebuilt)
            inputs.append({
                "snapshot": snapshot,
                "decision_at": at,
                "risk_only": risk_only,
            })

        result = compare_loss_cooldown(
            symbol=plan["symbol"],
            policy=policy,
            fee_bps=plan["fee_bps"],
            slippage_bps=plan["slippage_bps"],
            decision_inputs=inputs,
        )

    verify_cooldown_manifest(
        manifest,
        policy=policy,
        fee_bps=plan["fee_bps"],
        slippage_bps=plan["slippage_bps"],
    )
    result.update({
        "source_validation_plan_id": expected_plan_id,
        "source_validation_sha256": expected_sha256,
        "comparison_manifest": manifest,
        "provider_evidence": deepcopy(evidence),
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

    if canonical(report) != canonical(result):
        raise ValueError("Comparison replay or report metadata differs.")

    return {
        "status": "matched",
        "decision_count": ticks,
        "source_validation_plan_id": expected_plan_id,
        "source_validation_sha256": expected_sha256,
        "comparison_manifest_sha256": manifest["manifest_sha256"],
        "comparison_preregistered": False,
        "promotion_authorized": False,
        "live_capital_authorized": False,
    }
