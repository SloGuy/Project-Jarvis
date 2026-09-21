"""Pairwise correlation of aligned, completed daily return intervals.

Consumes build_daily_returns outputs. Never fills missing intervals.
Correlation describes the supplied sample, not future diversification.
"""

from datetime import time, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from itertools import combinations

from app.capital.portfolio_daily_returns import utc_timestamp


def _return_value(value):
    if isinstance(value, bool):
        raise ValueError("Return must be finite and greater than -1.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("Invalid return value.") from error
    if not result.is_finite() or result <= -1:
        raise ValueError("Return must be finite and greater than -1.")
    return result


def _intervals(report):
    if report.get("methodology") != "completed_utc_daily_returns_v1":
        raise ValueError("Unsupported return methodology.")

    cutoff = utc_timestamp(report["as_of"])
    result = {}

    for row in report["returns"]:
        start = utc_timestamp(row["start"])
        end = utc_timestamp(row["end"])

        if (
            end - start != timedelta(days=1)
            or start.time() != time.max
            or end.time() != time.max
        ):
            raise ValueError("Returns must cover complete UTC daily intervals.")
        if end >= cutoff:
            raise ValueError("Return interval is not completed as of the report.")

        key = (start, end)
        if key in result:
            raise ValueError("Duplicate return interval.")
        result[key] = _return_value(row["return_fraction"])

    return cutoff, result


def analyze_correlations(*, reports_by_portfolio, minimum_observations=30):
    """Calculate Pearson correlation for each pair on exact shared intervals.

    Portfolio IDs are positive integers. Each report must come from the
    same as-of timestamp. The minimum sample is an explicit diagnostic
    policy; reaching it does not establish statistical significance.

    Pairwise samples may differ. This output is not a covariance matrix
    suitable for portfolio risk calculations.
    """
    if (
        isinstance(minimum_observations, bool)
        or not isinstance(minimum_observations, int)
        or minimum_observations < 2
    ):
        raise ValueError("minimum_observations must be an integer of at least 2.")
    if not isinstance(reports_by_portfolio, dict):
        raise ValueError("reports_by_portfolio must be a dictionary.")

    prepared = {}
    cutoff = None

    for portfolio_id, report in reports_by_portfolio.items():
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
        prepared[portfolio_id] = intervals

    pairs = []

    with localcontext() as context:
        context.prec = 60

        for left_id, right_id in combinations(sorted(prepared), 2):
            left = prepared[left_id]
            right = prepared[right_id]
            common = sorted(set(left) & set(right))
            count = len(common)

            pair = {
                "left_portfolio_id": left_id,
                "right_portfolio_id": right_id,
                "status": "insufficient_data",
                "correlation": None,
                "aligned_observations": count,
                "left_unmatched_observations": len(left) - count,
                "right_unmatched_observations": len(right) - count,
                "first_interval_start": (
                    common[0][0].isoformat() if common else None
                ),
                "last_interval_end": (
                    common[-1][1].isoformat() if common else None
                ),
            }

            if count >= minimum_observations:
                xs = [left[key] for key in common]
                ys = [right[key] for key in common]
                mean_x = sum(xs, Decimal("0")) / count
                mean_y = sum(ys, Decimal("0")) / count
                dx = [value - mean_x for value in xs]
                dy = [value - mean_y for value in ys]
                xx = sum((value * value for value in dx), Decimal("0"))
                yy = sum((value * value for value in dy), Decimal("0"))

                if xx == 0 or yy == 0:
                    pair["status"] = "undefined_constant_returns"
                else:
                    xy = sum(
                        (x * y for x, y in zip(dx, dy)),
                        Decimal("0"),
                    )
                    correlation = xy / (xx * yy).sqrt()
                    # Bound numerical rounding at the Pearson limits.
                    correlation = max(
                        Decimal("-1"), min(Decimal("1"), correlation)
                    )
                    pair["status"] = "available"
                    pair["correlation"] = str(correlation)

            pairs.append(pair)

    return {
        "methodology": "pairwise_aligned_daily_pearson_v1",
        "as_of": cutoff.isoformat() if cutoff is not None else None,
        "minimum_observations": minimum_observations,
        "portfolio_count": len(prepared),
        "pairs": pairs,
        "limitations": [
            "Only exact shared daily intervals are compared.",
            "Missing intervals are excluded without interpolation.",
            "Pairwise samples can differ and are not a joint risk matrix.",
            "Minimum sample size does not prove statistical significance.",
            "Input eligibility depends on upstream valuation checks.",
            "Historical data availability is not independently verified.",
        ],
        "database_writes": False,
        "allocation_authority": False,
        "live_capital_authority": False,
    }
