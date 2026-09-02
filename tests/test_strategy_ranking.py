from app.capital.strategy_ranking import (
    rank_strategies,
    score_strategy,
)


def performance(
    *,
    strategy_name: str,
    closed_trade_count: int,
    total_return_percent: float = 8.0,
    excess_return_percent: float = 4.0,
    maximum_drawdown_percent: float = 4.0,
    expectancy_usd: float = 0.75,
    profit_factor: float | None = 1.60,
    profit_factor_status: str = "calculated",
    sharpe_ratio_zero_rate: float = 1.10,
    win_rate_percent: float = 58.0,
) -> dict:
    return {
        "strategy_name": strategy_name,
        "experiment_id": (
            f"{strategy_name}_experiment"
        ),
        "portfolio_id": 1,
        "closed_trade_count": closed_trade_count,
        "total_return_percent": (
            total_return_percent
        ),
        "excess_return_percent": (
            excess_return_percent
        ),
        "maximum_drawdown_percent": (
            maximum_drawdown_percent
        ),
        "expectancy_usd": expectancy_usd,
        "profit_factor": profit_factor,
        "profit_factor_status": (
            profit_factor_status
        ),
        "sharpe_ratio_zero_rate": (
            sharpe_ratio_zero_rate
        ),
        "win_rate_percent": win_rate_percent,
    }


mature_strategy = score_strategy(
    performance(
        strategy_name="mature_v1",
        closed_trade_count=120,
    )
)

assert mature_strategy["capital_score"] > 0
assert (
    mature_strategy["capital_score"]
    == mature_strategy["raw_performance_score"]
)
assert (
    mature_strategy["evidence"]["label"]
    == "substantial"
)
assert mature_strategy["evidence"]["factor"] == 1.0

early_lucky_strategy = score_strategy(
    performance(
        strategy_name="early_lucky_v1",
        closed_trade_count=5,
        total_return_percent=20.0,
        excess_return_percent=20.0,
        maximum_drawdown_percent=0.0,
        expectancy_usd=5.0,
        profit_factor=None,
        profit_factor_status="no_losses",
        sharpe_ratio_zero_rate=5.0,
        win_rate_percent=100.0,
    )
)

assert (
    early_lucky_strategy["raw_performance_score"]
    == 100.0
)
assert (
    early_lucky_strategy["capital_score"]
    == 35.0
)
assert (
    early_lucky_strategy["evidence"]["label"]
    == "insufficient"
)

zero_trade_strategy = score_strategy(
    performance(
        strategy_name="zero_trade_v1",
        closed_trade_count=0,
    )
)

assert zero_trade_strategy["capital_score"] == 0.0
assert (
    zero_trade_strategy["evidence"]["label"]
    == "none"
)

worst_case_strategy = score_strategy(
    performance(
        strategy_name="worst_case_v1",
        closed_trade_count=120,
        total_return_percent=-20.0,
        excess_return_percent=-20.0,
        maximum_drawdown_percent=20.0,
        expectancy_usd=-2.0,
        profit_factor=0.0,
        sharpe_ratio_zero_rate=-2.0,
        win_rate_percent=0.0,
    )
)

assert worst_case_strategy["capital_score"] == 0.0

rankings = rank_strategies(
    [
        performance(
            strategy_name="early_lucky_v1",
            closed_trade_count=5,
            total_return_percent=20.0,
            excess_return_percent=20.0,
            maximum_drawdown_percent=0.0,
            expectancy_usd=5.0,
            profit_factor=None,
            profit_factor_status="no_losses",
            sharpe_ratio_zero_rate=5.0,
            win_rate_percent=100.0,
        ),
        performance(
            strategy_name="mature_v1",
            closed_trade_count=120,
        ),
    ]
)

assert rankings[0]["strategy_name"] == "mature_v1"
assert rankings[0]["rank"] == 1
assert rankings[1]["strategy_name"] == "early_lucky_v1"
assert rankings[1]["rank"] == 2

assert all(
    0.0 <= ranking["capital_score"] <= 100.0
    for ranking in rankings
)

assert all(
    "component_scores" in ranking
    and "weighted_components" in ranking
    for ranking in rankings
)

print("capital_score_boundaries: PASS")
print("evidence_adjustment: PASS")
print("early_luck_protection: PASS")
print("deterministic_ranking: PASS")
print("score_explainability: PASS")
print("live_capital_authority: NONE")
