"""Versioned prospective validation-plan contracts.

Hashes detect changes relative to a retained digest. They are not signatures.
This module does not authorize promotion or reserve market-data periods.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json


CRITERIA = {
    "minimum_completed_trades",
    "maximum_stale_tick_percent",
    "maximum_unusable_regular_tick_percent",
    "minimum_return_percent",
    "minimum_excess_return_percent",
    "maximum_drawdown_percent",
}
FIELDS = {
    "schema_version", "designation", "created_at", "created_by",
    "research", "strategy_version", "asset_id", "symbol", "provider",
    "start", "end_exclusive", "fee_bps", "slippage_bps",
    "benchmark", "criteria", "policy", "execution_manifest",
}
RESEARCH_FIELDS = {
    "research_id", "hypothesis_version", "strategy_name", "hypothesis",
    "asset_universe", "success_criteria",
}
BENCHMARK = "initial_10_percent_buy_and_hold_90_percent_cash"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def plan_digest(plan):
    return hashlib.sha256(canonical(plan)).hexdigest()


def timestamp(value):
    require(isinstance(value, str), "Timestamp must be text.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.utcoffset() is not None, "Timezone is required.")
    return parsed.astimezone(timezone.utc)


def number(value, name):
    require(isinstance(value, str), f"{name} must be a decimal string.")
    try:
        result = Decimal(value)
    except Exception as error:
        raise ValueError(f"Invalid {name}.") from error
    require(result.is_finite(), f"{name} must be finite.")
    return result


def research_snapshot(candidate):
    data = candidate.to_dict()
    return deepcopy({key: data[key] for key in RESEARCH_FIELDS})


def validate_plan(plan):
    require(isinstance(plan, dict) and set(plan) == FIELDS,
            "Unexpected validation-plan fields.")
    require(type(plan["schema_version"]) is int
            and plan["schema_version"] == 1, "Unsupported schema.")
    require(plan["designation"] == "prospective_validation",
            "Unsupported designation.")
    for name in ("created_by", "strategy_version", "symbol", "provider"):
        require(isinstance(plan[name], str) and bool(plan[name].strip()),
                f"{name} must not be empty.")

    research = plan["research"]
    require(isinstance(research, dict) and set(research) == RESEARCH_FIELDS,
            "Invalid research binding.")
    for name in ("research_id", "strategy_name", "hypothesis"):
        require(isinstance(research[name], str) and bool(research[name].strip()),
                f"Invalid research {name}.")
    require(type(research["hypothesis_version"]) is int
            and research["hypothesis_version"] > 0,
            "Hypothesis version must be positive.")
    for name in ("asset_universe", "success_criteria"):
        values = research[name]
        require(isinstance(values, list) and bool(values) and all(
            isinstance(value, str) and bool(value.strip()) for value in values
        ), f"Invalid research {name}.")
    require(plan["symbol"].upper() in {
        value.strip().upper() for value in research["asset_universe"]
    }, "Asset is outside the research universe.")
    require(type(plan["asset_id"]) is int and plan["asset_id"] > 0,
            "Asset ID must be positive.")
    require(plan["provider"] in {"Finnhub", "CoinGecko"},
            "Unsupported provider.")

    created = timestamp(plan["created_at"])
    start, end = timestamp(plan["start"]), timestamp(plan["end_exclusive"])
    require(created < start < end, "Validation must start after plan creation.")
    seconds = (end - start).total_seconds()
    require(seconds % 60 == 0 and seconds <= 10000 * 60,
            "Use whole-minute intervals up to 10,000 minutes.")
    for name in ("fee_bps", "slippage_bps"):
        require(0 <= number(plan[name], name) < 10000,
                f"{name} is outside the supported range.")
    require(plan["benchmark"] == BENCHMARK, "Unsupported benchmark.")
    for name in ("policy", "execution_manifest"):
        require(isinstance(plan[name], dict) and bool(plan[name]),
                f"{name} must be captured.")

    criteria = plan["criteria"]
    require(isinstance(criteria, dict) and set(criteria) == CRITERIA,
            "All acceptance criteria must be explicit.")
    require(type(criteria["minimum_completed_trades"]) is int
            and criteria["minimum_completed_trades"] > 0,
            "A positive completed-trade minimum is required.")
    for name in CRITERIA - {"minimum_completed_trades"}:
        value = number(criteria[name], name)
        if name.startswith("maximum_"):
            require(0 <= value <= 100, f"{name} must be between 0 and 100.")
    canonical(plan)
    return plan


def seal_plan(draft, *, now=None):
    require("created_at" not in draft,
            "Creation time is assigned when sealing the plan.")
    now = now if now is not None else datetime.now(timezone.utc)
    require(now.utcoffset() is not None, "Creation time must be timezone-aware.")
    plan = deepcopy(draft)
    plan["created_at"] = now.astimezone(timezone.utc).isoformat()
    validate_plan(plan)
    return {"plan": plan, "sha256": plan_digest(plan)}


def verify_plan(envelope, *, expected_sha256):
    require(isinstance(envelope, dict)
            and set(envelope) == {"plan", "sha256"}, "Invalid envelope.")
    validate_plan(envelope["plan"])
    actual = plan_digest(envelope["plan"])
    require(actual == envelope["sha256"] == expected_sha256,
            "Validation plan changed.")
    return deepcopy(envelope["plan"])


def check_binding(plan, candidate, strategy_version, policy, manifest):
    validate_plan(plan)
    require(plan["research"] == research_snapshot(candidate),
            "Research hypothesis or criteria changed.")
    require(plan["strategy_version"] == strategy_version,
            "Strategy version changed.")
    require(plan["policy"] == policy, "Risk policy changed.")
    require(plan["execution_manifest"] == manifest,
            "Execution configuration changed.")
