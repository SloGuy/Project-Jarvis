from datetime import (
    datetime,
    timedelta,
    timezone,
)
from decimal import Decimal

from app.capital.committee_models import (
    CommitteeDecision,
    GateStatus,
)
from app.capital.committee_service import (
    _committee_decision,
)
from app.capital.experiment_registry import (
    ExperimentDefinition,
    ExperimentStatus,
)
from app.capital.graduation_gates import (
    build_graduation_gates,
)


experiment = ExperimentDefinition(
    experiment_id="synthetic_test",
    name="Synthetic Committee Test",
    strategy_name="synthetic_v1",
    portfolio_name="Synthetic Portfolio",
    status=ExperimentStatus.COMPLETED,
    execution_mode="autonomous_paper_trading",
    started_at=(
        datetime.now(timezone.utc)
        - timedelta(days=200)
    ),
    duration_days=180,
    starting_capital_usd=Decimal("1000.00"),
    risk_policy_name="synthetic_policy",
)

passing_performance = {
    "closed_trade_count": 120,
    "time_series_observation_count": 90,
    "total_return_percent": 12.0,
    "excess_return_percent": 7.0,
    "expectancy_usd": 1.25,
    "profit_factor": 1.75,
    "profit_factor_status": "calculated",
    "maximum_drawdown_percent": 6.0,
}

passing_gates = build_graduation_gates(
    experiment=experiment,
    performance=passing_performance,
)

assert all(
    gate.status == GateStatus.PASSED
    for gate in passing_gates
)

assert (
    _committee_decision(
        failed_count=0,
        pending_count=0,
    )
    == CommitteeDecision.PROMOTE
)

assert (
    _committee_decision(
        failed_count=0,
        pending_count=4,
    )
    == CommitteeDecision.CONTINUE
)

assert (
    _committee_decision(
        failed_count=1,
        pending_count=0,
    )
    == CommitteeDecision.REVISE
)

assert (
    _committee_decision(
        failed_count=3,
        pending_count=0,
    )
    == CommitteeDecision.KILL
)

failing_performance = {
    **passing_performance,
    "total_return_percent": -8.0,
    "excess_return_percent": -5.0,
    "expectancy_usd": -1.0,
    "profit_factor": 0.60,
    "maximum_drawdown_percent": 18.0,
}

failing_gates = build_graduation_gates(
    experiment=experiment,
    performance=failing_performance,
)

failed_count = sum(
    1
    for gate in failing_gates
    if gate.status == GateStatus.FAILED
)

assert failed_count >= 3

assert (
    _committee_decision(
        failed_count=failed_count,
        pending_count=0,
    )
    == CommitteeDecision.KILL
)

assert all(
    gate.gate_name
    not in {
        "live_execution_enabled",
        "automatic_live_promotion",
    }
    for gate in passing_gates
)

print("continue_path: PASS")
print("revise_path: PASS")
print("kill_path: PASS")
print("promote_gate_path: PASS")
print("automatic_live_authority: NONE")
