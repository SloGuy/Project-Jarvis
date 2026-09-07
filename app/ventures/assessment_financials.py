from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation


PREFIX = "Monthly financial history reported by source: "
FIELDS = ("gross_revenue", "net_profit", "expenses")


def _number(value, field):
    if value is None or isinstance(value, bool):
        raise ValueError(f"Missing or invalid {field}.")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid {field}.") from exc
    if not result.is_finite():
        raise ValueError(f"Nonfinite {field}.")
    if field != "net_profit" and result < 0:
        raise ValueError(f"Negative {field}.")
    return result


def _display(value):
    # Decimal strings preserve precision without float overflow.
    return str(value.quantize(Decimal("0.01")))


def _change(start, end):
    if start <= 0:
        return None
    return _display((end - start) / start * 100)


def summarize_financial_claim(claim: dict) -> dict:
    if claim.get("category") != "source_metrics":
        raise ValueError("Expected a source_metrics claim.")

    text = claim.get("claim", "")
    if not isinstance(text, str) or not text.startswith(PREFIX):
        raise ValueError("Unrecognized financial-history format.")

    raw = json.loads(text[len(PREFIX):])
    if not isinstance(raw, list) or not raw:
        raise ValueError("Financial history must be a nonempty list.")

    rows = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Invalid financial-history row.")
        if item.get("discarded_at") is not None:
            continue

        month = date.fromisoformat(item["month"])
        if month.day != 1:
            raise ValueError("Financial month must use its first day.")
        if month in seen:
            raise ValueError("Duplicate financial month.")
        seen.add(month)

        rows.append({
            "month": month,
            **{field: _number(item.get(field), field) for field in FIELDS},
        })

    if not rows:
        raise ValueError("No active financial-history rows.")

    rows.sort(key=lambda row: row["month"])
    gaps = []
    for previous, current in zip(rows, rows[1:]):
        a, b = previous["month"], current["month"]
        if b.year * 12 + b.month - (a.year * 12 + a.month) != 1:
            gaps.append({
                "after": a.isoformat(),
                "before": b.isoformat(),
            })

    def render(row):
        return {
            "month": row["month"].isoformat(),
            **{field: _display(row[field]) for field in FIELDS},
        }

    first, latest = rows[0], rows[-1]
    peak = max(rows, key=lambda row: row["net_profit"])

    return {
        "claim_id": claim["claim_id"],
        "source": claim["source"],
        "basis": "calculated_from_unverified_source_history",
        "currency": "USD",
        "month_count": len(rows),
        "gaps": gaps,
        "first_reported_month": render(first),
        "latest_reported_month": render(latest),
        "peak_reported_profit_month": render(peak),
        "first_to_latest_change_percent": {
            field: _change(first[field], latest[field])
            for field in ("gross_revenue", "net_profit")
        },
        "peak_profit_month_to_latest_change_percent": {
            field: _change(peak[field], latest[field])
            for field in ("gross_revenue", "net_profit")
        },
        "recent_reported_months": [render(row) for row in rows[-4:]],
        "limitations": [
            "Calculations do not independently verify source figures.",
            "Net profit is not assumed to be SDE.",
            "Peak comparison is descriptive, not a forecast.",
            "Percentage changes are omitted when the starting value "
            "is zero or negative.",
            "Reported months may be incomplete or out of date.",
        ],
    }
