from datetime import datetime, timezone
from typing import Any

from app.capital.committee_models import (
    GateStatus,
    GraduationGate,
)
from app.capital.experiment_registry import (
    ExperimentDefinition,
)
from app.capital.safety_audit import (
    get_capital_safety_audit,
)


MINIMUM_CLOSED_TRADES = 100
MINIMUM_RISK_OBSERVATIONS = 60
MINIMUM_PROFIT_FACTOR = 1.20
MAXIMUM_DRAWDOWN_PERCENT = 10.0


def _gate(
    *,
    name: str,
    status: GateStatus,
    actual: Any,
    required: Any,
    rationale: str,
) -> GraduationGate:
    return GraduationGate(
        gate_name=name,
        status=status,
        actual_value=actual,
        required_value=required,
        rationale=rationale,
    )


def _metric_gate(
    *,
    name: str,
    actual: float | None,
    required: str,
    passed: bool,
    mature: bool,
) -> GraduationGate:
    if not mature:
        status = GateStatus.PENDING
        rationale = (
            "Performance evidence remains immature."
        )
    else:
        status = (
            GateStatus.PASSED
            if passed
            else GateStatus.FAILED
        )
        rationale = (
            "Mature evidence satisfies the gate."
            if passed
            else "Mature evidence does not satisfy "
            "the gate."
        )

    return _gate(
        name=name,
        status=status,
        actual=actual,
        required=required,
        rationale=rationale,
    )


def build_graduation_gates(
    *,
    experiment: ExperimentDefinition,
    performance: dict[str, Any],
) -> tuple[GraduationGate, ...]:
    now = datetime.now(timezone.utc)

    elapsed_days = (
        max(
            0,
            (now - experiment.started_at).days,
        )
        if experiment.started_at is not None
        else 0
    )

    closed_trades = int(
        performance["closed_trade_count"]
    )
    risk_observations = int(
        performance[
            "time_series_observation_count"
        ]
    )

    trade_sample_mature = (
        closed_trades
        >= MINIMUM_CLOSED_TRADES
    )

    duration_complete = (
        elapsed_days
        >= experiment.duration_days
    )

    risk_sample_mature = (
        risk_observations
        >= MINIMUM_RISK_OBSERVATIONS
    )

    profit_factor = performance.get(
        "profit_factor"
    )
    no_losses = (
        performance.get(
            "profit_factor_status"
        )
        == "no_losses"
        and closed_trades > 0
    )

    safety_audit = (
        get_capital_safety_audit()
    )

    drawdown = float(
        performance[
            "maximum_drawdown_percent"
        ]
    )

    gates = [
        _gate(
            name="experiment_duration",
            status=(
                GateStatus.PASSED
                if duration_complete
                else GateStatus.PENDING
            ),
            actual=elapsed_days,
            required=experiment.duration_days,
            rationale=(
                "Full experiment duration completed."
                if duration_complete
                else "Experiment is still collecting "
                "time-based evidence."
            ),
        ),
        _gate(
            name="closed_trade_sample",
            status=(
                GateStatus.PASSED
                if trade_sample_mature
                else GateStatus.PENDING
            ),
            actual=closed_trades,
            required=MINIMUM_CLOSED_TRADES,
            rationale=(
                "Required trade sample collected."
                if trade_sample_mature
                else "More completed trades are required."
            ),
        ),
        _gate(
            name="time_series_sample",
            status=(
                GateStatus.PASSED
                if risk_sample_mature
                else GateStatus.PENDING
            ),
            actual=risk_observations,
            required=MINIMUM_RISK_OBSERVATIONS,
            rationale=(
                "Risk time series is substantial."
                if risk_sample_mature
                else "More daily equity observations "
                "are required."
            ),
        ),
        _metric_gate(
            name="positive_total_return",
            actual=float(
                performance[
                    "total_return_percent"
                ]
            ),
            required="> 0%",
            passed=(
                float(
                    performance[
                        "total_return_percent"
                    ]
                )
                > 0
            ),
            mature=trade_sample_mature,
        ),
        _metric_gate(
            name="positive_excess_return",
            actual=performance.get(
                "excess_return_percent"
            ),
            required="> 0% versus SPY",
            passed=(
                performance.get(
                    "excess_return_percent"
                )
                is not None
                and float(
                    performance[
                        "excess_return_percent"
                    ]
                )
                > 0
            ),
            mature=trade_sample_mature,
        ),
        _metric_gate(
            name="positive_expectancy",
            actual=performance.get(
                "expectancy_usd"
            ),
            required="> $0 per completed trade",
            passed=(
                performance.get(
                    "expectancy_usd"
                )
                is not None
                and float(
                    performance["expectancy_usd"]
                )
                > 0
            ),
            mature=trade_sample_mature,
        ),
        _metric_gate(
            name="profit_factor",
            actual=(
                "no_losses"
                if no_losses
                else profit_factor
            ),
            required=(
                f">= {MINIMUM_PROFIT_FACTOR:.2f}"
            ),
            passed=(
                no_losses
                or (
                    profit_factor is not None
                    and float(profit_factor)
                    >= MINIMUM_PROFIT_FACTOR
                )
            ),
            mature=trade_sample_mature,
        ),
        _metric_gate(
            name="maximum_drawdown",
            actual=drawdown,
            required=(
                f"<= {MAXIMUM_DRAWDOWN_PERCENT:.1f}%"
            ),
            passed=(
                drawdown
                <= MAXIMUM_DRAWDOWN_PERCENT
            ),
            mature=(
                trade_sample_mature
                and risk_sample_mature
            ),
        ),
        _gate(
            name="paper_execution_boundary",
            status=(
                GateStatus.PASSED
                if experiment.execution_mode
                == "autonomous_paper_trading"
                else GateStatus.FAILED
            ),
            actual=experiment.execution_mode,
            required="autonomous_paper_trading",
            rationale=(
                "Graduation evidence must come from "
                "isolated paper execution."
            ),
        ),
        _gate(
            name="operational_safety_audit",
            status=(
                GateStatus.PASSED
                if safety_audit["status"]
                == "passed"
                else GateStatus.FAILED
            ),
            actual=safety_audit["status"],
            required="passed",
            rationale=(
                "All portfolio, experiment, research, "
                "paper-execution, and live-capital "
                "invariants must pass."
            ),
        ),
        _gate(
            name="live_capital_lock",
            status=GateStatus.PASSED,
            actual=False,
            required=False,
            rationale=(
                "Live capital remains disabled and "
                "requires explicit human authorization."
            ),
        ),
    ]

    return tuple(gates)
