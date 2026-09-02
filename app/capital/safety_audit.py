from datetime import datetime, timezone
from typing import Any

from app.capital.experiment_registry import (
    list_experiments,
)
from app.capital.portfolio_status import (
    get_strategy_portfolios,
)
from app.capital.research_service import (
    list_research_candidates,
)
from app.capital.strategy_registry import (
    list_strategies,
)


LIVE_CAPITAL_ENABLED = False
HUMAN_APPROVAL_REQUIRED = True


def utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _check(
    *,
    name: str,
    passed: bool,
    actual: Any,
    required: Any,
    rationale: str,
) -> dict[str, Any]:
    return {
        "check_name": name,
        "passed": passed,
        "actual_value": actual,
        "required_value": required,
        "rationale": rationale,
    }


def get_capital_safety_audit() -> dict[str, Any]:
    strategies = list_strategies()
    experiments = list_experiments()
    portfolios = get_strategy_portfolios()
    research_candidates = (
        list_research_candidates()
    )

    strategy_names = [
        strategy.name
        for strategy in strategies
    ]

    experiment_strategy_names = [
        experiment.strategy_name
        for experiment in experiments
    ]

    experiment_portfolio_names = [
        experiment.portfolio_name
        for experiment in experiments
    ]

    valid_portfolios = [
        portfolio
        for portfolio in portfolios
        if portfolio.get(
            "portfolio_status"
        )
        == "success"
    ]

    portfolio_ids = [
        int(portfolio["portfolio_id"])
        for portfolio in valid_portfolios
    ]

    research_strategy_names = [
        candidate.strategy_name
        for candidate in research_candidates
    ]

    checks = [
        _check(
            name="live_capital_disabled",
            passed=(
                LIVE_CAPITAL_ENABLED is False
            ),
            actual=LIVE_CAPITAL_ENABLED,
            required=False,
            rationale=(
                "Jarvis Capital V1 must remain "
                "paper-only."
            ),
        ),
        _check(
            name="human_approval_required",
            passed=(
                HUMAN_APPROVAL_REQUIRED is True
            ),
            actual=HUMAN_APPROVAL_REQUIRED,
            required=True,
            rationale=(
                "Any future live-capital transition "
                "requires explicit human approval."
            ),
        ),
        _check(
            name="strategy_experiment_mapping",
            passed=(
                set(strategy_names)
                == set(
                    experiment_strategy_names
                )
            ),
            actual=experiment_strategy_names,
            required=strategy_names,
            rationale=(
                "Every registered executable strategy "
                "must have exactly one governed "
                "experiment."
            ),
        ),
        _check(
            name="unique_experiment_strategies",
            passed=(
                len(experiment_strategy_names)
                == len(
                    set(
                        experiment_strategy_names
                    )
                )
            ),
            actual=len(
                experiment_strategy_names
            ),
            required="all unique",
            rationale=(
                "Strategies may not share experiment "
                "identity."
            ),
        ),
        _check(
            name="unique_experiment_portfolios",
            passed=(
                len(experiment_portfolio_names)
                == len(
                    set(
                        experiment_portfolio_names
                    )
                )
            ),
            actual=experiment_portfolio_names,
            required="all unique",
            rationale=(
                "Experiments must use isolated "
                "portfolio identities."
            ),
        ),
        _check(
            name="portfolio_resolution",
            passed=(
                len(valid_portfolios)
                == len(experiments)
            ),
            actual=len(valid_portfolios),
            required=len(experiments),
            rationale=(
                "Every experiment portfolio must "
                "resolve successfully."
            ),
        ),
        _check(
            name="unique_portfolio_ids",
            passed=(
                len(portfolio_ids)
                == len(set(portfolio_ids))
            ),
            actual=portfolio_ids,
            required="all unique",
            rationale=(
                "Strategies may not share a portfolio."
            ),
        ),
        _check(
            name="paper_execution_only",
            passed=all(
                experiment.execution_mode
                == "autonomous_paper_trading"
                for experiment in experiments
            ),
            actual=[
                experiment.execution_mode
                for experiment in experiments
            ],
            required=[
                "autonomous_paper_trading"
            ],
            rationale=(
                "All V1 experiments must execute only "
                "against paper portfolios."
            ),
        ),
        _check(
            name="research_execution_isolation",
            passed=(
                set(research_strategy_names)
                .isdisjoint(strategy_names)
            ),
            actual=research_strategy_names,
            required=(
                "No research-only candidate registered "
                "as executable"
            ),
            rationale=(
                "Research candidates cannot execute "
                "before governed implementation and "
                "registration."
            ),
        ),
    ]

    failed_checks = [
        check
        for check in checks
        if not check["passed"]
    ]

    return {
        "status": (
            "passed"
            if not failed_checks
            else "failed"
        ),
        "generated_at": utc_now_iso(),
        "check_count": len(checks),
        "passed_check_count": (
            len(checks) - len(failed_checks)
        ),
        "failed_check_count": len(
            failed_checks
        ),
        "live_capital_enabled": (
            LIVE_CAPITAL_ENABLED
        ),
        "human_approval_required": (
            HUMAN_APPROVAL_REQUIRED
        ),
        "checks": checks,
    }
