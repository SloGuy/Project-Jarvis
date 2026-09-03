from app.capital.allocator import (
    build_shadow_allocation,
)


rankings = [
    {
        "rank": 1,
        "strategy_name": "momentum_v1",
        "capital_score": 60.0,
        "evidence": {
            "label": "developing",
        },
    },
    {
        "rank": 2,
        "strategy_name": "mean_reversion_v1",
        "capital_score": 30.0,
        "evidence": {
            "label": "insufficient",
        },
    },
]

committee_reports = [
    {
        "strategy_name": "momentum_v1",
        "decision": "continue",
    },
    {
        "strategy_name": "mean_reversion_v1",
        "decision": "continue",
    },
]

regime_results = [
    {
        "strategy_name": "momentum_v1",
        "regime": "range_bound_low_volatility",
        "trade_count": 10,
        "expectancy_usd": 0.25,
        "win_rate_percent": 40.0,
    },
]

allocation = build_shadow_allocation(
    rankings=rankings,
    committee_reports=committee_reports,
    current_regime=(
        "range_bound_low_volatility"
    ),
    regime_results=regime_results,
)

by_strategy = {
    item["strategy_name"]: item
    for item in allocation["recommendations"]
}

assert allocation["mode"] == "shadow"
assert allocation["deployed_percent"] <= 60.0
assert allocation["cash_reserve_percent"] >= 40.0

assert (
    by_strategy["momentum_v1"][
        "recommended_allocation_percent"
    ]
    <= 35.0
)

assert (
    by_strategy["mean_reversion_v1"][
        "recommended_allocation_percent"
    ]
    <= 15.0
)

assert (
    allocation["deployed_percent"]
    + allocation["cash_reserve_percent"]
    == 100.0
)

blocked_reports = [
    committee_reports[0],
    {
        "strategy_name": "mean_reversion_v1",
        "decision": "kill",
    },
]

blocked = build_shadow_allocation(
    rankings=rankings,
    committee_reports=blocked_reports,
    current_regime="uncertain",
    regime_results=[],
)

blocked_by_strategy = {
    item["strategy_name"]: item
    for item in blocked["recommendations"]
}

assert (
    blocked_by_strategy[
        "mean_reversion_v1"
    ]["recommended_allocation_percent"]
    == 0.0
)

assert allocation["paper_portfolio_writes"] is False
assert (
    allocation["paper_execution_authority"]
    is False
)
assert (
    allocation["live_capital_authority"]
    is False
)

print("cash_reserve_enforcement: PASS")
print("total_allocation_enforcement: PASS")
print("evidence_cap_enforcement: PASS")
print("committee_exclusion: PASS")
print("regime_adjustment: PASS")
print("paper_portfolio_writes: NONE")
print("paper_execution_authority: NONE")
print("live_capital_authority: NONE")
