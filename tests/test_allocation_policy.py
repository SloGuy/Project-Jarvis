from app.capital.allocation_policy import (
    CAPITAL_V2_SHADOW_POLICY,
)


policy = CAPITAL_V2_SHADOW_POLICY

assert (
    policy.minimum_cash_reserve_percent
    >= 40
)
assert (
    policy.maximum_total_allocation_percent
    <= 60
)
assert (
    policy.minimum_cash_reserve_percent
    + policy.maximum_total_allocation_percent
    == 100
)
assert (
    policy.maximum_strategy_allocation_percent
    <= policy.maximum_total_allocation_percent
)
assert (
    policy.insufficient_evidence_cap_percent
    < policy.developing_evidence_cap_percent
    < policy.substantial_evidence_cap_percent
)
assert policy.minimum_regime_trade_count >= 10
assert policy.shadow_mode is True
assert policy.paper_execution_enabled is False
assert policy.live_execution_enabled is False

print("cash_reserve_boundary: PASS")
print("total_allocation_boundary: PASS")
print("evidence_caps: PASS")
print("shadow_mode: PASS")
print("paper_execution_authority: NONE")
print("live_execution_authority: NONE")
