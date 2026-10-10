"""Schedule experiment diagnostics and process one advisory proposal."""

from app.capital.autonomy_trade_research import (
    _authorize,
    queue_trade_research,
)
from app.capital.trade_research_runner import process_trade_research_once


def read_experiments():
    from app.capital.experiment_registry import list_experiments

    return [experiment.to_dict() for experiment in list_experiments()]


def run_trade_research_cycle():
    """Inspect running paper experiments without modifying their strategies.

    Individual diagnostic failures are reported separately. Other eligible
    experiments can still receive research proposals. Permission failures
    stop the cycle immediately.
    """
    _authorize()
    experiments = read_experiments()
    if not isinstance(experiments, list):
        raise ValueError("Invalid experiment inventory.")

    eligible = []
    identities = set()
    for experiment in experiments:
        identifier = experiment.get("experiment_id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("Invalid experiment identity.")
        if identifier in identities:
            raise ValueError("Duplicate experiment identity.")
        identities.add(identifier)
        if (
            experiment.get("status") == "running"
            and experiment.get("execution_mode") == "autonomous_paper_trading"
        ):
            eligible.append(identifier)

    outcomes = []
    scheduled_count = 0
    for identifier in sorted(eligible):
        _authorize()
        try:
            result = queue_trade_research(experiment_id=identifier)
        except PermissionError:
            raise
        except Exception as error:
            # Preserve failure visibility without emitting credentials or
            # potentially sensitive database/provider exception text.
            outcomes.append({
                "experiment_id": identifier,
                "status": "diagnosis_failed",
                "error_type": type(error).__name__,
            })
            continue

        scheduled_count += result["scheduled_count"]
        outcome = {
            "experiment_id": identifier,
            "status": result["status"],
        }
        request = result.get("request")
        if request is not None:
            outcome.update({
                "request_id": request["request_id"],
                "request_status": request["status"],
            })
        outcomes.append(outcome)

    _authorize()
    processing = process_trade_research_once()
    failures = sum(
        outcome["status"] == "diagnosis_failed"
        for outcome in outcomes
    )
    return {
        "status": "partial" if failures else "completed",
        "scope": "trade_diagnosis_and_advisory_research",
        "eligible_experiment_count": len(eligible),
        "scheduled_count": scheduled_count,
        "diagnostic_failure_count": failures,
        "scheduling": outcomes,
        "processing": processing,
        "research_candidate_changed": False,
        "strategy_change_authorized": False,
        "promotion_authorized": False,
        "live_capital_authorized": False,
    }
