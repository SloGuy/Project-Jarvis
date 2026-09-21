"""Drawdown overlap from eligible completed daily return intervals.

Each continuous segment starts at an index value of one.
Missing intervals reset the index and peak; gaps are never bridged.
"""

from decimal import Decimal, localcontext
from itertools import combinations

from app.capital.portfolio_correlation import _intervals


def _drawdowns(intervals):
    marks = {}
    index = Decimal("1")
    peak = Decimal("1")
    previous_end = None
    segments = 0
    maximum = Decimal("0")

    for (start, end), change in sorted(intervals.items()):
        if previous_end != start:
            index = Decimal("1")
            peak = Decimal("1")
            segments += 1

        index *= Decimal("1") + change
        peak = max(peak, index)
        drawdown = (peak - index) / peak
        maximum = max(maximum, drawdown)
        marks[(start, end)] = drawdown
        previous_end = end

    return marks, segments, maximum


def analyze_drawdown_overlap(
    *,
    reports_by_portfolio,
    minimum_observations=30,
):
    """Compare sampled drawdowns on exact shared return intervals.

    Drawdown means strictly below the segment's previous peak.
    This is descriptive overlap, not a prediction or risk allocation.
    """
    if not isinstance(reports_by_portfolio, dict):
        raise ValueError("reports_by_portfolio must be a dictionary.")
    if (
        isinstance(minimum_observations, bool)
        or not isinstance(minimum_observations, int)
        or minimum_observations < 2
    ):
        raise ValueError("minimum_observations must be an integer of at least 2.")

    prepared = {}
    summaries = []
    cutoff = None

    with localcontext() as context:
        context.prec = 60

        for portfolio_id, report in sorted(reports_by_portfolio.items()):
            if (
                isinstance(portfolio_id, bool)
                or not isinstance(portfolio_id, int)
                or portfolio_id <= 0
            ):
                raise ValueError("Portfolio IDs must be positive integers.")

            report_cutoff, intervals = _intervals(report)
            if cutoff is not None and report_cutoff != cutoff:
                raise ValueError("Return reports must have the same as-of time.")
            cutoff = report_cutoff

            marks, segments, maximum = _drawdowns(intervals)
            prepared[portfolio_id] = marks
            summaries.append({
                "portfolio_id": portfolio_id,
                "observation_count": len(marks),
                "continuous_segments": segments,
                "drawdown_observations": sum(
                    value > 0 for value in marks.values()
                ),
                "maximum_segment_drawdown_percent": (
                    str(maximum * 100) if marks else None
                ),
            })

        pairs = []
        for left_id, right_id in combinations(sorted(prepared), 2):
            left = prepared[left_id]
            right = prepared[right_id]
            common = sorted(set(left) & set(right))
            count = len(common)
            left_count = sum(left[key] > 0 for key in common)
            right_count = sum(right[key] > 0 for key in common)
            both_count = sum(
                left[key] > 0 and right[key] > 0
                for key in common
            )
            sufficient = count >= minimum_observations

            pairs.append({
                "left_portfolio_id": left_id,
                "right_portfolio_id": right_id,
                "status": "available" if sufficient else "insufficient_data",
                "aligned_observations": count,
                "left_drawdown_observations": left_count,
                "right_drawdown_observations": right_count,
                "simultaneous_drawdown_observations": both_count,
                "simultaneous_drawdown_percent": (
                    str(Decimal(both_count) / count * 100)
                    if sufficient else None
                ),
                "first_interval_start": (
                    common[0][0].isoformat() if common else None
                ),
                "last_interval_end": (
                    common[-1][1].isoformat() if common else None
                ),
            })

    return {
        "methodology": "segmented_daily_drawdown_overlap_v1",
        "as_of": cutoff.isoformat() if cutoff is not None else None,
        "minimum_observations": minimum_observations,
        "portfolios": summaries,
        "pairs": pairs,
        "limitations": [
            "Drawdowns are measured at daily endpoints, not intraday.",
            "Every missing interval resets the affected portfolio's peak.",
            "Segment maxima are not full-history maximum drawdowns.",
            "Overlap percentages use only exact shared intervals.",
            "Small samples and missing data can distort apparent overlap.",
            "Input eligibility depends on upstream valuation checks.",
        ],
        "database_writes": False,
        "allocation_authority": False,
        "live_capital_authority": False,
    }
