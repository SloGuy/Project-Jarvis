from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from app.ventures.assessment_financials import summarize_financial_claim


POLICY_VERSION = "review_priority_v1"


def review_priority(
    *,
    candidate: bool,
    research: dict | None,
    assessment_status: str,
    today: date | None = None,
) -> dict:
    today = today or date.today()
    reasons = []
    financial_flags = []
    summaries = []

    claims = research["report"]["claims"] if research else []
    metric_claims = [
        claim for claim in claims
        if claim["category"] == "source_metrics"
    ]

    for claim in metric_claims:
        try:
            summary = summarize_financial_claim(claim)
            summaries.append(summary)
            latest = summary["latest_reported_month"]
            month = date.fromisoformat(latest["month"])
            months_old = (
                (today.year - month.year) * 12 + today.month - month.month
            )

            if months_old < 0:
                financial_flags.append("Financial history contains future months.")
            elif months_old > 3:
                financial_flags.append(
                    "Latest reported financial month is more than three months old."
                )

            if summary["gaps"]:
                financial_flags.append("Reported financial history has missing months.")

            if Decimal(latest["net_profit"]) <= 0:
                financial_flags.append(
                    f"Reported net profit is nonpositive in {latest['month']}."
                )

            changes = summary["first_to_latest_change_percent"]
            first_month = summary["first_reported_month"]["month"]
            for field, title in (
                ("gross_revenue", "Revenue"),
                ("net_profit", "Net profit"),
            ):
                change = changes[field]
                if change is not None and Decimal(change) <= -25:
                    financial_flags.append(
                        f"{title} fell {abs(Decimal(change))}% between "
                        f"{first_month} and {latest['month']} "
                        "in the supplied history."
                    )
        except (ValueError, KeyError, TypeError, InvalidOperation):
            financial_flags.append(
                "A financial-history claim could not be summarized reliably."
            )

    if len(metric_claims) > 1:
        financial_flags.append(
            "Multiple financial-history versions require reconciliation."
        )

    if research and any(
        risk["severity"] == "high"
        for risk in research["report"]["risks"]
    ):
        financial_flags.append("Research contains an unresolved high-severity risk.")

    if not candidate:
        group = "outside_current_candidate_queue"
        order = 4
        reasons.append("Not currently eligible in the automatic research queue.")
    elif financial_flags:
        group = "investigate_risks"
        order = 0
        reasons.append(
            "Review these warning signals before relying on the acquisition case."
        )
    elif not research or not metric_claims:
        group = "collect_missing_evidence"
        order = 1
        reasons.append("No usable financial-history claim has been established.")
    elif assessment_status == "current_draft":
        group = "review_available_draft"
        order = 2
        reasons.append(
            "A model draft matches current research and configuration; "
            "its conclusions remain unreviewed."
        )
    else:
        group = "await_current_assessment"
        order = 3
        reasons.append("No model draft matches current research and configuration.")

    return {
        "policy_version": POLICY_VERSION,
        "group": group,
        "sort_order": order,
        "reasons": reasons + list(dict.fromkeys(financial_flags)),
        "financial_summaries": summaries,
        "limitations": [
            "Priority indicates review work, not acquisition attractiveness.",
            "The 25 percent decline threshold is a triage rule, not a valuation rule.",
            "Historical comparisons do not establish the cause of a change.",
            "Source financials remain unverified; absence of a flag is not "
            "evidence of a healthy business.",
        ],
    }
