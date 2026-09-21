"""Historical variance contributions using one common return sample.

Weights are supplied assumptions, not allocation recommendations.
No missing returns are filled and no portfolio is silently excluded.
"""

from decimal import Decimal, InvalidOperation, localcontext

from app.capital.portfolio_correlation import _intervals


def _weight(value):
    if isinstance(value, bool):
        raise ValueError("Weights must be finite and nonnegative.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("Invalid portfolio weight.") from error
    if not result.is_finite() or result < 0:
        raise ValueError("Weights must be finite and nonnegative.")
    return result


def analyze_risk_contribution(
    *,
    reports_by_portfolio,
    weights_by_portfolio,
    minimum_observations=30,
):
    """Calculate contributions to daily portfolio variance.

    Requires nonnegative weights summing to one. All supplied portfolios,
    including zero-weight portfolios, participate in sample alignment.
    Negative contributions are retained when covariance offsets risk.
    """
    if (
        not isinstance(reports_by_portfolio, dict)
        or not isinstance(weights_by_portfolio, dict)
    ):
        raise ValueError("Reports and weights must be dictionaries.")
    if not reports_by_portfolio:
        raise ValueError("At least one portfolio is required.")
    if set(reports_by_portfolio) != set(weights_by_portfolio):
        raise ValueError("Reports and weights must cover identical portfolios.")
    if (
        isinstance(minimum_observations, bool)
        or not isinstance(minimum_observations, int)
        or minimum_observations < 2
    ):
        raise ValueError("minimum_observations must be an integer of at least 2.")

    for mapping in (reports_by_portfolio, weights_by_portfolio):
        for portfolio_id in mapping:
            if (
                isinstance(portfolio_id, bool)
                or not isinstance(portfolio_id, int)
                or portfolio_id <= 0
            ):
                raise ValueError("Portfolio IDs must be positive integers.")

    ids = sorted(reports_by_portfolio)
    weights = [_weight(weights_by_portfolio[key]) for key in ids]
    prepared = {}
    cutoff = None

    for portfolio_id in ids:
        report_cutoff, intervals = _intervals(
            reports_by_portfolio[portfolio_id]
        )
        if cutoff is not None and cutoff != report_cutoff:
            raise ValueError("Return reports must have the same as-of time.")
        cutoff = report_cutoff
        prepared[portfolio_id] = intervals

    common = sorted(set.intersection(
        *(set(prepared[key]) for key in ids)
    ))
    count = len(common)

    result = {
        "methodology": "common_sample_daily_variance_contribution_v1",
        "as_of": cutoff.isoformat(),
        "status": "insufficient_data",
        "minimum_observations": minimum_observations,
        "aligned_observations": count,
        "first_interval_start": common[0][0].isoformat() if common else None,
        "last_interval_end": common[-1][1].isoformat() if common else None,
        "daily_variance": None,
        "daily_volatility_percent": None,
        "portfolios": [
            {
                "portfolio_id": portfolio_id,
                "weight_fraction": str(weight),
                "unmatched_observations": (
                    len(prepared[portfolio_id]) - count
                ),
                "variance_contribution": None,
                "risk_contribution_percent": None,
            }
            for portfolio_id, weight in zip(ids, weights)
        ],
        "limitations": [
            "Results describe historical daily variance, not future risk.",
            "Weights are explicit fixed assumptions for this calculation.",
            "Every portfolio uses the same complete-case sample.",
            "Missing intervals are excluded without interpolation.",
            "Negative contributions can represent covariance offsets.",
            "Input eligibility depends on upstream valuation checks.",
        ],
        "database_writes": False,
        "allocation_authority": False,
        "live_capital_authority": False,
    }

    with localcontext() as context:
        context.prec = 60

        if sum(weights, Decimal("0")) != Decimal("1"):
            raise ValueError("Portfolio weights must sum to one.")

        if count < minimum_observations:
            return result

        series = [
            [prepared[portfolio_id][key] for key in common]
            for portfolio_id in ids
        ]
        means = [
            sum(values, Decimal("0")) / count
            for values in series
        ]
        centered = [
            [value - mean for value in values]
            for values, mean in zip(series, means)
        ]
        combined = [
            sum(
                (
                    weights[index] * centered[index][day]
                    for index in range(len(ids))
                ),
                Decimal("0"),
            )
            for day in range(count)
        ]
        variance = sum(
            (value * value for value in combined), Decimal("0")
        ) / (count - 1)

        result["daily_variance"] = str(variance)
        result["daily_volatility_percent"] = str(variance.sqrt() * 100)

        if variance == 0:
            result["status"] = "undefined_zero_variance"
            return result

        for index, row in enumerate(result["portfolios"]):
            covariance = sum(
                (
                    own * total
                    for own, total in zip(centered[index], combined)
                ),
                Decimal("0"),
            ) / (count - 1)
            contribution = weights[index] * covariance
            row["variance_contribution"] = str(contribution)
            row["risk_contribution_percent"] = str(
                contribution / variance * 100
            )

        result["status"] = "available"

    return result
