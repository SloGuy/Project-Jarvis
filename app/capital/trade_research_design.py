"""Predefined prospective research design; no executable strategy change."""

from copy import deepcopy
import hashlib
import json


HYPOTHESIS = (
    "Does a predefined 60-minute same-asset re-entry cooldown after a "
    "losing closed trade improve cost-adjusted profit factor versus the "
    "unchanged baseline in a new prospective period, while preserving "
    "the existing acceptance criteria?"
)


def _digest(value):
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_research_design(diagnosis):
    """Describe a comparison requiring future implementation and registration.

    The cooldown is a research choice, not an improvement established by
    the historical journals. It never changes the existing experiment.
    """
    if not isinstance(diagnosis, dict):
        raise ValueError("A trade diagnosis is required.")
    if diagnosis.get("designation") != "retrospective_trade_diagnosis":
        raise ValueError("Unexpected diagnosis designation.")
    for field in (
        "experiment_id",
        "strategy_name",
        "journal_sha256",
        "configuration_sha256",
    ):
        value = diagnosis.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Missing diagnosis {field}.")
    thresholds = diagnosis.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("Diagnosis thresholds are missing.")

    design = {
        "schema_version": 1,
        "design_id": "same_asset_loss_cooldown_60m_v1",
        "origin_experiment_id": diagnosis["experiment_id"],
        "strategy_name": diagnosis["strategy_name"],
        "origin_journal_sha256": diagnosis["journal_sha256"],
        "origin_configuration_sha256": diagnosis["configuration_sha256"],
        "hypothesis": HYPOTHESIS,
        "selection_basis": (
            "Predefined research option prompted by weak realized profit "
            "factor; benefit has not been established."
        ),
        "baseline": "Unchanged strategy and policy in isolated paper replay.",
        "intervention": {
            "cooldown_seconds": 3600,
            "scope": "same_asset_within_the_test_strategy",
            "trigger": (
                "A completed trade with negative realized P/L, whose "
                "completion and outcome were available before the decision."
            ),
            "rule": (
                "Reject new entries while decision time is less than "
                "3600 seconds after the latest eligible losing close. "
                "An entry at exactly 3600 seconds is eligible."
            ),
            "protective_exits": "Remain enabled and unchanged.",
            "other_entry_and_risk_rules": "Remain unchanged.",
        },
        "comparison": {
            "inputs": (
                "Both variants use the same prospectively collected, "
                "verified quote stream and decision schedule."
            ),
            "accounts": (
                "Separate simulated accounts; neither changes production "
                "holdings or the other variant's trade history."
            ),
            "cooldown_history": (
                "Use only the intervention account's completed trades "
                "available before each decision."
            ),
            "costs": (
                "Identical fees and slippage, fixed before collection; "
                "cost assumptions still require registration."
            ),
            "asset_universe": (
                "Fixed before collection; no exclusions based on "
                "eventual exits or evaluation-period returns."
            ),
            "results": (
                "Report both variants, trade counts, profit factor, "
                "returns, drawdown and data quality, including failures "
                "and inconclusive outcomes."
            ),
        },
        "observed_committee_thresholds": deepcopy(thresholds),
        "registration_blockers": [
            "The cooldown comparison is not implemented in the replay engine.",
            "Baseline source/version and full acceptance criteria must be pinned.",
            "Asset universe, costs, benchmark and future window must be fixed.",
            "Outcome availability and cooldown timing must be tested.",
            "Comparison rules and insufficient-sample handling must be registered.",
        ],
        "historical_benefit_established": False,
        "validation_ready": False,
        "strategy_change_authorized": False,
        "promotion_authorized": False,
        "live_capital_authorized": False,
    }
    return {**design, "design_sha256": _digest(design)}


def verify_design(design, diagnosis):
    expected = build_research_design(diagnosis)
    if design != expected:
        raise ValueError("Research design differs from the predefined comparison.")
    return deepcopy(expected)


def validate_design_hypothesis(hypothesis):
    if not isinstance(hypothesis, str) or hypothesis.strip() != HYPOTHESIS:
        raise ValueError("Proposal departed from the predefined research design.")
