"""Assess development cooldown comparisons without promotion authority."""

from copy import deepcopy
from decimal import Decimal
import hashlib

from app.capital.cooldown_analysis import analyze_cooldown_packet
from app.capital.cooldown_verification import canonical
from app.capital.validation_assessment import assess_metrics
from app.capital.validation_plan import validate_plan, verify_plan


def _positive_decimal_text(value):
    if not isinstance(value, str):
        raise ValueError("Profit-factor minimum must be decimal text.")
    try:
        result = Decimal(value)
    except Exception as error:
        raise ValueError("Invalid profit-factor minimum.") from error
    if not result.is_finite() or result <= 0:
        raise ValueError("Profit-factor minimum must be finite and positive.")
    return result


def assess_comparison_metrics(
    plan,
    accounts,
    *,
    decision_ticks,
    availability_verified,
    minimum_completed_trades,
    minimum_profit_factor,
):
    """Apply explicit development criteria to measured account summaries."""
    validate_plan(plan)
    if (
        not isinstance(accounts, dict)
        or set(accounts) != {"baseline", "intervention"}
    ):
        raise ValueError("Both comparison accounts are required.")
    if type(decision_ticks) is not int or decision_ticks <= 0:
        raise ValueError("A positive decision count is required.")
    if type(availability_verified) is not bool:
        raise ValueError("Availability must be a boolean.")
    if (
        type(minimum_completed_trades) is not int
        or minimum_completed_trades
        < plan["criteria"]["minimum_completed_trades"]
    ):
        raise ValueError("Comparison must preserve the source trade minimum.")
    required_pf = _positive_decimal_text(minimum_profit_factor)

    assessments = {}
    insufficient = []
    factors = {}
    for name in ("baseline", "intervention"):
        summary = accounts[name]
        if type(summary.get("has_equity_marks")) is not bool:
            raise ValueError("Equity-mark availability must be boolean.")

        if summary["has_equity_marks"]:
            assessment = assess_metrics(
                plan,
                summary,
                decision_ticks=decision_ticks,
                availability_verified=availability_verified,
            )
        else:
            assessment = {
                "criteria_status": "insufficient_evidence",
                "validation_status": "insufficient_evidence",
                "failed_performance_criteria": [],
                "insufficient_evidence_reasons": [
                    "No equity marks are available."
                ],
                "performance_checks": {},
                "promotion_authorized": False,
            }

        assessments[name] = assessment
        insufficient.extend(
            f"{name}: {reason}"
            for reason in assessment["insufficient_evidence_reasons"]
        )

        trades = summary["completed_trades"]
        if type(trades) is not int or trades < 0:
            raise ValueError("Completed trades must be a nonnegative integer.")
        if trades < minimum_completed_trades:
            insufficient.append(
                f"{name}: sample is below the comparison trade minimum."
            )

        status = summary["profit_factor_status"]
        value = summary["profit_factor"]
        if status == "no_realized_losses" and value is None:
            factors[name] = None
            insufficient.append(
                f"{name}: realized profit factor is undefined."
            )
        elif status == "defined" and value is not None:
            if isinstance(value, bool):
                raise ValueError("Invalid measured profit factor.")
            factor = Decimal(str(value))
            if not factor.is_finite() or factor < 0:
                raise ValueError("Invalid measured profit factor.")
            factors[name] = factor
        else:
            raise ValueError("Profit-factor value and status disagree.")

    baseline_pf = factors["baseline"]
    intervention_pf = factors["intervention"]
    delta = (
        intervention_pf - baseline_pf
        if baseline_pf is not None and intervention_pf is not None
        else None
    )
    checks = {
        "minimum_intervention_profit_factor": {
            "actual": (
                str(intervention_pf) if intervention_pf is not None else None
            ),
            "required": minimum_profit_factor,
            "passed": (
                intervention_pf >= required_pf
                if intervention_pf is not None else None
            ),
        },
        "positive_profit_factor_improvement": {
            "actual": str(delta) if delta is not None else None,
            "required": "strictly greater than 0",
            "passed": delta > 0 if delta is not None else None,
        },
    }

    failed = list(
        assessments["intervention"]["failed_performance_criteria"]
    )
    failed.extend(
        name for name, check in checks.items()
        if check["passed"] is False
    )
    status = (
        "insufficient_evidence" if insufficient
        else "fail" if failed
        else "pass"
    )

    return {
        "schema_version": 1,
        "scope": "development_comparison_criteria",
        "comparison_criteria_status": status,
        "validation_status": "insufficient_evidence",
        "source_criteria": deepcopy(plan["criteria"]),
        "comparison_criteria": {
            "minimum_completed_trades_per_account": minimum_completed_trades,
            "minimum_intervention_profit_factor": minimum_profit_factor,
            "profit_factor_improvement": "strictly greater than 0",
        },
        "accounts": assessments,
        "comparison_checks": checks,
        "failed_comparison_criteria": failed,
        "insufficient_evidence_reasons": insufficient,
        "validation_blockers": [
            "This comparison and its acceptance criteria are not preregistered."
        ],
        "comparison_preregistered": False,
        "validation_ready": False,
        "strategy_change_authorized": False,
        "promotion_authorized": False,
        "live_capital_authorized": False,
    }


def assess_cooldown_packet(
    report,
    *,
    policy,
    minimum_completed_trades,
    minimum_profit_factor,
    **expected_references,
):
    analysis = analyze_cooldown_packet(
        report, policy=policy, **expected_references
    )
    plan = verify_plan(
        report["provider_evidence"]["envelope"],
        expected_sha256=expected_references["expected_sha256"],
    )
    result = assess_comparison_metrics(
        plan,
        analysis["accounts"],
        decision_ticks=report["decision_count"],
        availability_verified=analysis["input_availability_verified"],
        minimum_completed_trades=minimum_completed_trades,
        minimum_profit_factor=minimum_profit_factor,
    )
    result["verification"] = analysis["verification"]
    result["analysis"] = analysis
    result["input_report_sha256"] = hashlib.sha256(
        canonical(report).encode("utf-8")
    ).hexdigest()
    result["input_report_hash_kind"] = "canonical_content"
    return result
