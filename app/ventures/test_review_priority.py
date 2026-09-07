import json
from copy import deepcopy
from datetime import date

from app.ventures.assessment_financials import PREFIX
from app.ventures.review_priority import review_priority


def research(rows):
    return {
        "report": {
            "claims": [{
                "claim_id": "metrics_test",
                "category": "source_metrics",
                "source": "https://example.invalid/listing",
                "claim": PREFIX + json.dumps(rows),
            }],
            "risks": [],
        }
    }


def month(value, revenue, profit):
    return {
        "month": value,
        "gross_revenue": revenue,
        "net_profit": profit,
        "expenses": revenue - profit,
        "discarded_at": None,
    }


def priority(record, *, candidate=True, status="current_draft"):
    return review_priority(
        candidate=candidate,
        research=record,
        assessment_status=status,
        today=date(2026, 9, 7),
    )


def main():
    declining = research([
        month("2026-05-01", 5000, 3000),
        month("2026-06-01", 4000, 2000),
        month("2026-07-01", 3000, 1000),
        month("2026-08-01", 2000, 500),
    ])
    original = deepcopy(declining)
    result = priority(declining)
    assert result["group"] == "investigate_risks"
    assert any("Revenue fell 60.00%" in text for text in result["reasons"])
    assert any("Net profit fell 83.33%" in text for text in result["reasons"])
    assert declining == original
    print("financial_decline_visible_without_model: PASS")

    assert priority(None)["group"] == "collect_missing_evidence"
    assert priority({"report": {"claims": [], "risks": []}})["group"] == (
        "collect_missing_evidence"
    )

    stable = research([
        month("2026-07-01", 1000, 500),
        month("2026-08-01", 1000, 500),
    ])
    assert priority(stable)["group"] == "review_available_draft"
    assert priority(stable, status="stale_draft")["group"] == (
        "await_current_assessment"
    )
    assert priority(stable, status="not_assessed")["group"] == (
        "await_current_assessment"
    )
    assert priority(declining, candidate=False)["group"] == (
        "outside_current_candidate_queue"
    )
    print("missing_evidence_drafts_and_ineligible_candidates: PASS")

    old = research([month("2026-01-01", 1000, 500)])
    assert any("three months old" in reason for reason in priority(old)["reasons"])

    future = research([month("2026-10-01", 1000, 500)])
    assert any("future" in reason for reason in priority(future)["reasons"])

    gaps = research([
        month("2026-06-01", 1000, 500),
        month("2026-08-01", 1000, 500),
    ])
    assert any("missing months" in reason for reason in priority(gaps)["reasons"])

    loss = research([month("2026-08-01", 1000, -100)])
    assert any("nonpositive" in reason for reason in priority(loss)["reasons"])
    print("old_future_missing_months_and_losses: PASS")

    malformed = deepcopy(stable)
    malformed["report"]["claims"][0]["claim"] = PREFIX + "invalid JSON"
    assert priority(malformed)["group"] == "investigate_risks"

    versions = deepcopy(stable)
    versions["report"]["claims"].append(dict(
        versions["report"]["claims"][0], claim_id="metrics_second_version"
    ))
    assert any(
        "Multiple financial-history versions" in reason
        for reason in priority(versions)["reasons"]
    )

    high_risk = deepcopy(stable)
    high_risk["report"]["risks"] = [{
        "severity": "high",
        "category": "source_disclosure_change",
        "description": "Synthetic unresolved change.",
    }]
    assert priority(high_risk)["group"] == "investigate_risks"
    print("malformed_history_versions_and_unresolved_risks: PASS")


if __name__ == "__main__":
    main()
