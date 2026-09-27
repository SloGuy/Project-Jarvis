"""Descriptive analytics for aligned hourly sampled indicative returns."""
from decimal import Decimal, localcontext
from itertools import combinations

from app.capital.portfolio_daily_returns import utc_timestamp
from app.capital.portfolio_sampled_returns import METHOD


def _number(value):
    if isinstance(value, bool):
        raise ValueError("Invalid numeric input.")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Nonfinite numeric input.")
    return result


def _prepare(reports):
    prepared = {}
    cutoff = None
    for identifier, report in sorted(reports.items()):
        if type(identifier) is not int or identifier <= 0:
            raise ValueError("Invalid portfolio ID.")
        if report["methodology"] != METHOD:
            raise ValueError("Expected sampled hourly returns.")
        at = utc_timestamp(report["as_of"])
        if cutoff is not None and at != cutoff:
            raise ValueError("Report cutoffs differ.")
        cutoff = at
        intervals = {}
        previous_end = None
        for row in sorted(report["returns"], key=lambda item: item["start"]):
            start = utc_timestamp(row["start"])
            end = utc_timestamp(row["end"])
            seconds = (end - start).total_seconds()
            if not 3300 <= seconds <= 3900 or end >= cutoff:
                raise ValueError("Invalid completed sampled interval.")
            if previous_end is not None and start < previous_end:
                raise ValueError("Overlapping sampled intervals.")
            previous_end = end
            key = (start, end)
            if key in intervals:
                raise ValueError("Duplicate interval.")
            value = _number(row["return_fraction"])
            if value <= -1:
                raise ValueError("Return must exceed -1.")
            intervals[key] = value
        prepared[identifier] = intervals
    return cutoff, prepared


def _center(values):
    mean = sum(values, Decimal("0")) / len(values)
    return [value - mean for value in values]


def _drawdowns(intervals):
    index = peak = Decimal("1")
    previous_end = None
    marks = {}
    for (start, end), change in sorted(intervals.items()):
        if start != previous_end:
            index = peak = Decimal("1")
        index *= 1 + change
        peak = max(peak, index)
        marks[(start, end)] = (peak - index) / peak
        previous_end = end
    return marks


def analyze_sampled_returns(
    *, reports_by_portfolio, weights_by_portfolio=None, minimum_observations=30
):
    """Weights, when supplied, are explicit fixed diagnostic assumptions.

    Thirty hourly observations are not thirty days or proof of significance.
    No annualization, allocation recommendation, or gap filling is performed.
    """
    if type(minimum_observations) is not int or minimum_observations < 2:
        raise ValueError("Minimum observations must be an integer of at least 2.")
    cutoff, prepared = _prepare(reports_by_portfolio)
    correlations = []
    overlaps = []
    risk = {"status": "weights_not_supplied", "portfolios": []}

    with localcontext() as context:
        context.prec = 60
        drawdowns = {
            identifier: _drawdowns(intervals)
            for identifier, intervals in prepared.items()
        }
        for left, right in combinations(sorted(prepared), 2):
            common = sorted(set(prepared[left]) & set(prepared[right]))
            count = len(common)
            base = {
                "left_portfolio_id": left,
                "right_portfolio_id": right,
                "aligned_observations": count,
                "status": "insufficient_data",
            }
            pair = {**base, "correlation": None}
            overlap = {**base, "simultaneous_drawdown_percent": None}
            if count >= minimum_observations:
                xs = _center([prepared[left][key] for key in common])
                ys = _center([prepared[right][key] for key in common])
                xx = sum((x * x for x in xs), Decimal("0"))
                yy = sum((y * y for y in ys), Decimal("0"))
                if xx == 0 or yy == 0:
                    pair["status"] = "undefined_constant_returns"
                else:
                    xy = sum((x * y for x, y in zip(xs, ys)), Decimal("0"))
                    value = xy / (xx * yy).sqrt()
                    pair.update(
                        status="available_indicative",
                        correlation=str(max(Decimal("-1"), min(Decimal("1"), value))),
                    )
                both = sum(
                    drawdowns[left][key] > 0 and drawdowns[right][key] > 0
                    for key in common
                )
                overlap.update(
                    status="available_indicative",
                    simultaneous_drawdown_percent=str(Decimal(both) / count * 100),
                )
            correlations.append(pair)
            overlaps.append(overlap)

        if weights_by_portfolio is not None:
            if not prepared or set(weights_by_portfolio) != set(prepared):
                raise ValueError("Weights must cover exactly all report portfolios.")
            weights = {
                key: _number(value) for key, value in weights_by_portfolio.items()
            }
            if any(value < 0 for value in weights.values()):
                raise ValueError("Negative weights are unsupported.")
            if sum(weights.values(), Decimal("0")) != 1:
                raise ValueError("Weights must sum to one.")

            common = sorted(set.intersection(
                *(set(rows) for rows in prepared.values())
            ))
            count = len(common)
            risk = {
                "status": "insufficient_data",
                "aligned_observations": count,
                "sampled_interval_variance": None,
                "sampled_interval_volatility_percent": None,
                "portfolios": [
                    {
                        "portfolio_id": key,
                        "weight_fraction": str(weights[key]),
                        "variance_contribution": None,
                        "risk_contribution_percent": None,
                    }
                    for key in sorted(prepared)
                ],
            }
            if count >= minimum_observations:
                centered = {
                    key: _center([rows[interval] for interval in common])
                    for key, rows in prepared.items()
                }
                combined = [
                    sum(
                        (weights[key] * centered[key][i] for key in prepared),
                        Decimal("0"),
                    )
                    for i in range(count)
                ]
                variance = sum(
                    (value * value for value in combined), Decimal("0")
                ) / (count - 1)
                risk.update(
                    status=(
                        "available_indicative" if variance != 0
                        else "undefined_zero_variance"
                    ),
                    sampled_interval_variance=str(variance),
                    sampled_interval_volatility_percent=str(variance.sqrt() * 100),
                )
                if variance != 0:
                    for row in risk["portfolios"]:
                        key = row["portfolio_id"]
                        covariance = sum(
                            (x * y for x, y in zip(centered[key], combined)),
                            Decimal("0"),
                        ) / (count - 1)
                        contribution = weights[key] * covariance
                        row["variance_contribution"] = str(contribution)
                        row["risk_contribution_percent"] = str(
                            contribution / variance * 100
                        )

    return {
        "methodology": "aligned_hourly_sampled_analytics_v1",
        "as_of": cutoff.isoformat() if cutoff else None,
        "minimum_observations": minimum_observations,
        "correlation": {"pairs": correlations},
        "drawdown_overlap": {"pairs": overlaps},
        "risk_contribution": risk,
        "limitations": [
            "All results describe sampled indicative valuations.",
            "Actual interval lengths vary within the hourly capture tolerance.",
            "Pairwise samples may differ; risk uses one common sample.",
            "Drawdown peaks reset after missing intervals.",
            "Weights are fixed diagnostic assumptions, not recommendations.",
            "Thirty hourly observations are not thirty days of evidence.",
            "No annualization or historical completeness claim is made.",
        ],
        "database_writes": False,
        "allocation_authority": False,
        "live_capital_authority": False,
    }
