import json
from copy import deepcopy

from app.ventures.assessment_financials import (
    PREFIX,
    summarize_financial_claim,
)


def row(month, revenue, profit, expenses):
    return {
        "month": month,
        "gross_revenue": revenue,
        "net_profit": profit,
        "expenses": expenses,
        "discarded_at": None,
    }


def summarize(rows):
    return summarize_financial_claim({
        "claim_id": "synthetic_metrics",
        "category": "source_metrics",
        "source": "https://example.invalid/listing",
        "claim": PREFIX + json.dumps(rows),
    })


def expect_invalid(rows):
    try:
        summarize(rows)
    except (ValueError, KeyError, TypeError):
        return
    raise AssertionError("Expected invalid history rejection")


def main():
    rows = [
        row("2026-05-01", "5178.96", "3922.01", "1256.95"),
        row("2026-06-01", "3644.60", "2392.69", "1251.91"),
        row("2026-07-01", "1834.98", "617.31", "1217.67"),
        row("2026-08-01", "1346.10", "128.43", "1217.67"),
    ]
    before = deepcopy(rows)
    result = summarize(list(reversed(rows)))

    assert result["month_count"] == 4
    assert result["gaps"] == []
    assert result["latest_reported_month"]["month"] == "2026-08-01"
    assert result["peak_profit_month_to_latest_change_percent"] == {
        "gross_revenue": "-74.01",
        "net_profit": "-96.73",
    }
    assert result["claim_id"] == "synthetic_metrics"
    assert result["basis"] == "calculated_from_unverified_source_history"
    assert rows == before
    print("financial_math_ordering_and_provenance: PASS")

    gap = summarize([rows[0], rows[2]])
    assert gap["gaps"] == [{
        "after": "2026-05-01",
        "before": "2026-07-01",
    }]
    year_boundary = summarize([
        row("2025-12-01", 100, 50, 50),
        row("2026-01-01", 120, 60, 60),
    ])
    assert year_boundary["gaps"] == []
    print("missing_months_and_year_boundary: PASS")

    zero = summarize([
        row("2026-01-01", 0, 0, 0),
        row("2026-02-01", 100, -20, 120),
    ])
    assert zero["first_to_latest_change_percent"] == {
        "gross_revenue": None,
        "net_profit": None,
    }
    negative = summarize([
        row("2026-01-01", 100, -50, 150),
        row("2026-02-01", 100, -20, 120),
    ])
    assert negative["first_to_latest_change_percent"]["net_profit"] is None
    assert negative["latest_reported_month"]["net_profit"] == "-20.00"
    print("zero_and_negative_baselines: PASS")

    discarded = dict(rows[0], discarded_at="2026-09-01")
    assert summarize([discarded, rows[1]])["month_count"] == 1

    expect_invalid([rows[0], rows[0]])
    expect_invalid([discarded])
    expect_invalid([])
    for field, value in (
        ("gross_revenue", None),
        ("gross_revenue", True),
        ("gross_revenue", -1),
        ("expenses", "NaN"),
        ("net_profit", "Infinity"),
        ("month", "2026-05-15"),
        ("month", "invalid"),
    ):
        expect_invalid([dict(rows[0], **{field: value})])
    print("invalid_and_discarded_rows: PASS")


if __name__ == "__main__":
    main()
