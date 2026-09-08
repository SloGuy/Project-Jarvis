"""Explicit shadow controls and backward-looking downward sizing."""
from decimal import Decimal as D


def controls(strategies, risk_mode, size_scales):
    if risk_mode not in {"normal", "reduce_only", "halted"}:
        raise ValueError("Unknown shadow risk mode.")
    supplied = {} if size_scales is None else size_scales
    if set(supplied) - set(strategies):
        raise ValueError("Scale references an unknown strategy.")
    result = {}
    for strategy in strategies:
        scale = D(str(supplied.get(strategy, "1")))
        if not scale.is_finite() or not 0 <= scale <= 1:
            raise ValueError("Sizing scales must be finite and between zero and one.")
        result[strategy] = scale
    return result


def downward_volatility_scale(prices, *, target_volatility_percent, minimum_returns=20):
    """Population standard deviation of consecutive observation returns.

    Target and observed volatility use the same observation interval.
    No annualization, forecasting, or upward scaling is performed.
    """
    target = D(str(target_volatility_percent))
    if not target.is_finite() or target <= 0:
        raise ValueError("A positive finite volatility target is required.")
    if type(minimum_returns) is not int or minimum_returns < 2:
        raise ValueError("At least two returns are required.")
    values = [D(str(price)) for price in prices]
    if any(not value.is_finite() or value <= 0 for value in values):
        raise ValueError("Prices must be positive and finite.")
    returns = [
        (current / previous - 1) * 100
        for previous, current in zip(values, values[1:])
    ]
    if len(returns) < minimum_returns:
        return {"scale": D("0"), "observed_volatility_percent": None,
                "reason": "Insufficient return observations."}
    mean = sum(returns, D("0")) / len(returns)
    variance = sum(((value - mean) ** 2 for value in returns), D("0")) / len(returns)
    volatility = variance.sqrt()
    return {
        "scale": min(D("1"), target / volatility) if volatility else D("1"),
        "observed_volatility_percent": volatility,
        "reason": "Downward-only observation-return scaling.",
    }
