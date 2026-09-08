"""Assess measured criteria without granting promotion authority."""
from decimal import Decimal

from app.capital.validation_plan import validate_plan, plan_digest
from app.capital.replay_analysis import analyze_report


def decimal(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Assessment values must be finite.")
    return result


def assess_metrics(plan, summary, *, decision_ticks, availability_verified):
    validate_plan(plan)
    if type(decision_ticks) is not int or decision_ticks <= 0:
        raise ValueError("A positive decision count is required.")
    criteria = plan["criteria"]
    quality = summary["data_quality"]
    regular = quality.get("regular_ticks", 0)
    stale = quality.get("missing_or_stale_reference_ticks", 0)
    unusable = quality.get("unusable_regular_ticks", 0)
    trades = summary["completed_trades"]
    for value in (regular, stale, unusable, trades):
        if type(value) is not int or value < 0:
            raise ValueError("Evidence counts must be nonnegative integers.")
    if regular > decision_ticks or stale > decision_ticks or unusable > regular:
        raise ValueError("Inconsistent evidence counts.")

    insufficient = []
    if trades < criteria["minimum_completed_trades"]:
        insufficient.append("Completed-trade sample is below the planned minimum.")
    if regular == 0:
        insufficient.append("No regular decision ticks are available.")
    stale_percent = decimal(stale) / decision_ticks * 100
    unusable_percent = (
        decimal(unusable) / regular * 100 if regular else None
    )
    if stale_percent > decimal(criteria["maximum_stale_tick_percent"]):
        insufficient.append("Stale-reference coverage exceeds the planned limit.")
    if (
        unusable_percent is not None
        and unusable_percent > decimal(criteria["maximum_unusable_regular_tick_percent"])
    ):
        insufficient.append("Unusable-window coverage exceeds the planned limit.")

    benchmark = summary["benchmark"]
    if benchmark.get("entry_at") is None:
        insufficient.append("The planned benchmark has no entry.")

    actual_return = decimal(summary["return_percent"])
    excess = actual_return - decimal(benchmark["return_percent"])
    drawdown = decimal(summary["maximum_marked_drawdown_percent"])
    if drawdown < 0:
        raise ValueError("Drawdown must not be negative.")
    checks = {
        "minimum_return_percent": (
            actual_return, actual_return >= decimal(criteria["minimum_return_percent"])
        ),
        "minimum_excess_return_percent": (
            excess, excess >= decimal(criteria["minimum_excess_return_percent"])
        ),
        "maximum_drawdown_percent": (
            drawdown, drawdown <= decimal(criteria["maximum_drawdown_percent"])
        ),
    }
    failed = [name for name, (_, passed) in checks.items() if not passed]
    criteria_status = (
        "insufficient_evidence" if insufficient else "fail" if failed else "pass"
    )
    reasons = list(insufficient)
    if availability_verified is not True:
        reasons.append("Historical data availability remains unverified.")

    return {
        "schema_version": 1,
        "criteria_status": criteria_status,
        "validation_status": (
            "insufficient_evidence" if reasons else criteria_status
        ),
        "promotion_authorized": False,
        "failed_performance_criteria": failed,
        "insufficient_evidence_reasons": reasons,
        "measurements": {
            "completed_trades": trades,
            "decision_ticks": decision_ticks,
            "regular_ticks": regular,
            "stale_tick_percent": str(stale_percent),
            "unusable_regular_tick_percent": (
                str(unusable_percent) if unusable_percent is not None else None
            ),
        },
        "performance_checks": {
            name: {
                "actual": str(actual),
                "required": criteria[name],
                "passed": passed,
            }
            for name, (actual, passed) in checks.items()
        },
        "scope": "Specified-cost scenario against predeclared numeric criteria.",
    }


def assess_report(plan, report):
    validate_plan(plan)
    if report.get("designation") != "prospective_validation":
        raise ValueError("Development reports cannot be assessed as validation.")
    registration = report.get("validation_registration", {})
    if registration.get("sha256") != plan_digest(plan):
        raise ValueError("Report does not reference this validation plan.")
    for key in (
        "asset_id", "symbol", "provider", "start", "end_exclusive",
        "policy", "execution_manifest",
    ):
        if report.get(key) != plan[key]:
            raise ValueError(f"Report differs from plan: {key}")
    scenario = report["scenarios"]["specified_costs"]
    for key in ("fee_bps", "slippage_bps"):
        if decimal(scenario[key]) != decimal(plan[key]):
            raise ValueError(f"Scenario differs from plan: {key}")

    # This recomputes offline verification and analysis from the report.
    analysis = analyze_report(report)
    result = assess_metrics(
        plan, analysis["scenarios"]["specified_costs"],
        decision_ticks=len(report["windows"]),
        availability_verified=report.get("availability_verified"),
    )
    result["plan_sha256"] = plan_digest(plan)
    result["validation_registration"] = registration
    result["limitations"] = analysis["limitations"]
    return result
