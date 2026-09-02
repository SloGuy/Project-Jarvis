from typing import Any

from app.capital.performance_lab import (
    get_performance_lab,
)


SCORE_WEIGHTS = {
    "total_return": 20.0,
    "excess_return": 10.0,
    "maximum_drawdown": 20.0,
    "expectancy": 15.0,
    "profit_factor": 15.0,
    "sharpe_ratio": 10.0,
    "win_rate": 10.0,
}


def _clamp(
    value: float,
    minimum: float = 0.0,
    maximum: float = 100.0,
) -> float:
    return max(minimum, min(maximum, value))


def _linear_score(
    value: Any,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if value is None or maximum <= minimum:
        return 0.0

    normalized = (
        (float(value) - minimum)
        / (maximum - minimum)
        * 100.0
    )

    return _clamp(normalized)


def _inverse_score(
    value: Any,
    *,
    best: float,
    worst: float,
) -> float:
    if value is None or worst <= best:
        return 0.0

    normalized = (
        (worst - float(value))
        / (worst - best)
        * 100.0
    )

    return _clamp(normalized)


def _evidence_adjustment(
    closed_trade_count: int,
) -> tuple[str, float]:
    if closed_trade_count <= 0:
        return "none", 0.0

    if closed_trade_count < 30:
        return "insufficient", 0.35

    if closed_trade_count < 100:
        return "developing", 0.70

    return "substantial", 1.0


def _profit_factor_score(
    performance: dict[str, Any],
) -> float:
    closed_trades = int(
        performance.get("closed_trade_count")
        or 0
    )

    if (
        performance.get("profit_factor_status")
        == "no_losses"
        and closed_trades > 0
    ):
        return 100.0

    return _linear_score(
        performance.get("profit_factor"),
        minimum=0.0,
        maximum=2.0,
    )


def score_strategy(
    performance: dict[str, Any],
) -> dict[str, Any]:
    closed_trades = int(
        performance.get("closed_trade_count")
        or 0
    )

    evidence_label, evidence_factor = (
        _evidence_adjustment(closed_trades)
    )

    components = {
        "total_return": _linear_score(
            performance.get("total_return_percent"),
            minimum=-10.0,
            maximum=10.0,
        ),
        "excess_return": _linear_score(
            performance.get("excess_return_percent"),
            minimum=-10.0,
            maximum=10.0,
        ),
        "maximum_drawdown": _inverse_score(
            performance.get(
                "maximum_drawdown_percent"
            ),
            best=0.0,
            worst=10.0,
        ),
        "expectancy": _linear_score(
            performance.get("expectancy_usd"),
            minimum=-1.0,
            maximum=1.0,
        ),
        "profit_factor": _profit_factor_score(
            performance
        ),
        "sharpe_ratio": _linear_score(
            performance.get(
                "sharpe_ratio_zero_rate"
            ),
            minimum=-1.0,
            maximum=2.0,
        ),
        "win_rate": _linear_score(
            performance.get("win_rate_percent"),
            minimum=0.0,
            maximum=100.0,
        ),
    }

    weighted_components = {
        name: (
            components[name]
            * SCORE_WEIGHTS[name]
            / 100.0
        )
        for name in SCORE_WEIGHTS
    }

    raw_score = sum(
        weighted_components.values()
    )

    capital_score = raw_score * evidence_factor

    return {
        "strategy_name": performance[
            "strategy_name"
        ],
        "experiment_id": performance[
            "experiment_id"
        ],
        "portfolio_id": performance[
            "portfolio_id"
        ],
        "capital_score": round(
            capital_score,
            2,
        ),
        "raw_performance_score": round(
            raw_score,
            2,
        ),
        "evidence": {
            "label": evidence_label,
            "factor": evidence_factor,
            "closed_trade_count": closed_trades,
        },
        "component_scores": {
            name: round(score, 2)
            for name, score in components.items()
        },
        "weighted_components": {
            name: round(score, 2)
            for name, score
            in weighted_components.items()
        },
        "performance_summary": {
            "total_return_percent": (
                performance.get(
                    "total_return_percent"
                )
            ),
            "excess_return_percent": (
                performance.get(
                    "excess_return_percent"
                )
            ),
            "maximum_drawdown_percent": (
                performance.get(
                    "maximum_drawdown_percent"
                )
            ),
            "expectancy_usd": performance.get(
                "expectancy_usd"
            ),
            "profit_factor": performance.get(
                "profit_factor"
            ),
            "sharpe_ratio_zero_rate": (
                performance.get(
                    "sharpe_ratio_zero_rate"
                )
            ),
            "win_rate_percent": performance.get(
                "win_rate_percent"
            ),
        },
    }


def rank_strategies(
    strategies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rankings = [
        score_strategy(strategy)
        for strategy in strategies
    ]

    rankings.sort(
        key=lambda ranking: (
            -ranking["capital_score"],
            -ranking["raw_performance_score"],
            ranking["strategy_name"],
        )
    )

    for rank, ranking in enumerate(
        rankings,
        start=1,
    ):
        ranking["rank"] = rank

    return rankings


def get_strategy_rankings() -> dict[str, Any]:
    performance_lab = get_performance_lab()

    rankings = rank_strategies(
        performance_lab["strategies"]
    )

    return {
        "status": "success",
        "methodology_version": (
            "capital_score_v1"
        ),
        "advisory_only": True,
        "live_capital_authority": False,
        "strategy_count": len(rankings),
        "weights": SCORE_WEIGHTS,
        "rankings": rankings,
    }
