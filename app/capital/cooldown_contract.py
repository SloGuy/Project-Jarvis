"""Immutable comparison terms bound to a plan and completed research request.

Building a contract does not register it or authorize collection.
"""

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.capital import autonomy_trade_research as queue
from app.capital.cooldown_manifest import capture_cooldown_manifest, _plain
from app.capital.cooldown_verification import canonical
from app.capital.replay_manifest import verify_replay_manifest
from app.capital.trade_research_runner import _verify_completed
from app.capital.validation_input_contract import validate_input_contract
from app.capital.validation_plan import timestamp, verify_plan


MINIMUM_LEAD = timedelta(minutes=30)


def _origin(request):
    if not isinstance(request, dict):
        raise ValueError("A saved research request is required.")
    queue._verify_request(request, request.get("request_id"))
    if request["status"] != "completed":
        raise ValueError("Research request must be completed.")
    _verify_completed(request)

    diagnosis = request["diagnosis"]
    if diagnosis["strategy_name"] != "mean_reversion_v2":
        raise ValueError("Comparison currently supports Mean Reversion V2.")
    if (
        diagnosis.get("query_limit_reached") is not False
        or diagnosis.get("configuration_rechecked") is not True
        or queue._digest(diagnosis["configuration"])
        != diagnosis["configuration_sha256"]
    ):
        raise ValueError("Origin diagnosis is incomplete or inconsistent.")

    thresholds = diagnosis["thresholds"]
    minimum = thresholds["minimum_closed_trades"]
    if type(minimum) is not int or minimum <= 0:
        raise ValueError("Invalid origin trade minimum.")

    factor_minimum = Decimal(str(thresholds["minimum_profit_factor"]))
    if not factor_minimum.is_finite() or factor_minimum <= 0:
        raise ValueError("Invalid origin profit-factor minimum.")

    summary = diagnosis["summary"]
    count = summary["closed_trades"]
    factor = summary["profit_factor"]
    if type(count) is not int or count < minimum or factor is None:
        raise ValueError("Origin does not have a mature profit-factor failure.")
    factor = Decimal(str(factor))
    if not factor.is_finite() or factor < 0 or factor >= factor_minimum:
        raise ValueError("Origin does not have a mature profit-factor failure.")

    for field in (
        "strategy_change_authorized",
        "promotion_authorized",
        "live_capital_authorized",
    ):
        if request.get(field) is not False:
            raise ValueError("Unexpected origin authority.")

    return diagnosis, minimum, factor_minimum


def _terms(source_row, request, policy, created_at):
    plan = verify_plan(
        source_row["envelope"],
        expected_sha256=source_row["registered_sha256"],
    )
    if (
        plan["schema_version"] != 2
        or plan["research"]["strategy_name"] != "mean_reversion_v2"
        or plan["symbol"] != "BTC"
        or plan["provider"] != "CoinGecko"
        or [
            value.strip().upper()
            for value in plan["research"]["asset_universe"]
        ] != ["BTC"]
    ):
        raise ValueError("Comparison requires a version-two MR2/BTC plan.")

    if canonical(asdict(policy)) != canonical(plan["policy"]):
        raise ValueError("Comparison policy differs from baseline plan.")
    validate_input_contract(
        plan["input_contract"], policy=plan["policy"], verify_sources=True
    )
    verify_replay_manifest(plan["execution_manifest"])
    diagnosis, minimum, factor_minimum = _origin(request)

    created = timestamp(created_at)
    if (
        created < timestamp(plan["created_at"])
        or created < timestamp(request["completed_at"])
        or created >= timestamp(plan["start"])
    ):
        raise ValueError("Contract must follow its origin and precede the period.")

    # When checking a retained contract, its creation must also precede
    # collection binding. A new contract is built before any binding.
    collection = source_row.get("provider_collection")
    if collection is not None:
        if created >= timestamp(collection["bound_at"]):
            raise ValueError("Contract must precede collection binding.")

    return {
        "schema_version": 1,
        "kind": "prospective_loss_cooldown_comparison",
        "created_at": created.isoformat(),
        "source_plan_id": source_row["plan_id"],
        "source_plan_sha256": source_row["registered_sha256"],
        "origin_request_id": request["request_id"],
        "origin_request_sha256": queue._digest(request),
        "origin_diagnosis_sha256": request["diagnosis_sha256"],
        "research_design": deepcopy(request["research_design"]),
        "scope": "single_asset_BTC_comparison",
        "origin_scope_limit": (
            "Whole-experiment journal diagnosis motivates this BTC test; "
            "the result does not establish an experiment-wide benefit."
        ),
        "start": plan["start"],
        "end_exclusive": plan["end_exclusive"],
        "schedule": {
            "step_seconds": 60,
            "regular_every_ticks": 5,
            "regular_tick_offset": 0,
        },
        "asset_id": plan["asset_id"],
        "symbol": plan["symbol"],
        "provider": plan["provider"],
        "benchmark": plan["benchmark"],
        "fee_bps": plan["fee_bps"],
        "slippage_bps": plan["slippage_bps"],
        "source_criteria": deepcopy(plan["criteria"]),
        "comparison_criteria": {
            "minimum_completed_trades_per_account": max(
                100, minimum, plan["criteria"]["minimum_completed_trades"]
            ),
            "minimum_intervention_profit_factor": str(
                max(Decimal("1.2"), factor_minimum)
            ),
            "profit_factor_improvement": "strictly greater than 0",
            "intervention_source_performance_criteria": "all must pass",
            "baseline_performance_failures": "reported without exclusion",
            "insufficient_evidence": (
                "Either account lacks its required sample, usable evidence, "
                "equity marks, benchmark entry, or defined profit factor."
            ),
            "failed_checks": "retained when evidence is insufficient",
            "period_extension": "no automatic extension or retrospective retry",
        },
        "comparison_manifest": capture_cooldown_manifest(
            policy=policy,
            fee_bps=plan["fee_bps"],
            slippage_bps=plan["slippage_bps"],
        ),
        "registration_verified": False,
        "strategy_change_authorized": False,
        "promotion_authorized": False,
        "live_capital_authorized": False,
    }


def build_cooldown_contract(
    *, source_row, request, policy, now=None
):
    measured = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(measured, datetime) or measured.utcoffset() is None:
        raise ValueError("Creation time must include timezone.")
    measured = measured.astimezone(timezone.utc)

    if source_row["status"] != "registered":
        raise ValueError("Baseline plan must still be registered.")
    if (
        "provider_collection" in source_row
        or "witness_collection" in source_row
    ):
        raise ValueError("Build the contract before collection binding.")
    start = timestamp(source_row["envelope"]["plan"]["start"])
    if start - measured < MINIMUM_LEAD:
        raise ValueError("Comparison requires 30 minutes of lead time.")

    body = _plain(_terms(
        source_row, request, policy, measured.isoformat()
    ))
    return {"contract": body, "sha256": queue._digest(body)}


def verify_cooldown_contract(
    saved, *, expected_sha256, source_row, request, policy
):
    if not isinstance(saved, dict) or set(saved) != {"contract", "sha256"}:
        raise ValueError("Invalid comparison contract envelope.")
    body = saved["contract"]
    if not isinstance(body, dict):
        raise ValueError("Comparison contract must be a dictionary.")
    if queue._digest(body) != saved["sha256"] or saved["sha256"] != expected_sha256:
        raise ValueError("Comparison contract hash changed.")

    expected = _plain(_terms(
        source_row, request, policy, body["created_at"]
    ))
    if canonical(body) != canonical(expected):
        raise ValueError("Comparison terms or originating evidence changed.")
    return deepcopy(body)
