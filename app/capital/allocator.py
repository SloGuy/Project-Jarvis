from decimal import Decimal
from typing import Any

from app.capital.allocation_policy import (
    AllocationPolicy,
    CAPITAL_V2_SHADOW_POLICY,
)

from app.capital.committee_service import (
    get_capital_committee,
)
from app.capital.market_regime import (
    get_market_regime,
)
from app.capital.regime_performance import (
    get_regime_performance,
)
from app.capital.strategy_ranking import (
    get_strategy_rankings,
)


def _evidence_cap(
    *,
    label: str,
    policy: AllocationPolicy,
) -> Decimal:
    if label == "substantial":
        return (
            policy
            .substantial_evidence_cap_percent
        )

    if label == "developing":
        return (
            policy
            .developing_evidence_cap_percent
        )

    if label == "insufficient":
        return (
            policy
            .insufficient_evidence_cap_percent
        )

    return Decimal("0")


def _regime_adjustment(
    *,
    strategy_name: str,
    current_regime: str,
    regime_results: list[dict[str, Any]],
    policy: AllocationPolicy,
) -> tuple[Decimal, str]:
    matching = next(
        (
            result
            for result in regime_results
            if (
                result["strategy_name"]
                == strategy_name
                and result["regime"]
                == current_regime
            )
        ),
        None,
    )

    if matching is None:
        return (
            Decimal("1.00"),
            "No matching regime evidence.",
        )

    trade_count = int(
        matching["trade_count"]
    )

    if (
        trade_count
        < policy.minimum_regime_trade_count
    ):
        return (
            Decimal("1.00"),
            "Matching regime sample is immature.",
        )

    expectancy = Decimal(
        str(matching["expectancy_usd"])
    )

    win_rate = Decimal(
        str(matching["win_rate_percent"])
    )

    if expectancy < 0:
        return (
            Decimal("0.75"),
            "Negative regime expectancy applied "
            "a defensive penalty.",
        )

    if expectancy > 0 and win_rate >= 50:
        return (
            Decimal("1.15"),
            "Positive regime expectancy and win rate "
            "earned a bounded boost.",
        )

    if expectancy > 0:
        return (
            Decimal("1.05"),
            "Positive regime expectancy earned "
            "a small bounded boost.",
        )

    return (
        Decimal("1.00"),
        "Matching regime evidence is neutral.",
    )


def _next_action(
    *,
    committee_decision: str,
    evidence_label: str,
) -> str:
    if committee_decision == "kill":
        return "retirement_review"

    if committee_decision == "revise":
        return "return_to_research"

    if committee_decision == "promote":
        return "human_promotion_review"

    if evidence_label != "substantial":
        return "continue_experiment"

    return "committee_reassessment"


def build_shadow_allocation(
    *,
    rankings: list[dict[str, Any]],
    committee_reports: list[dict[str, Any]],
    current_regime: str,
    regime_results: list[dict[str, Any]],
    policy: AllocationPolicy = (
        CAPITAL_V2_SHADOW_POLICY
    ),
) -> dict[str, Any]:
    committee_by_strategy = {
        report["strategy_name"]: report
        for report in committee_reports
    }

    candidates = []

    for ranking in rankings:
        strategy_name = ranking[
            "strategy_name"
        ]

        committee = committee_by_strategy.get(
            strategy_name
        )

        decision = (
            committee.get("decision")
            if committee
            else "unavailable"
        )

        evidence_label = ranking[
            "evidence"
        ]["label"]

        evidence_cap = min(
            _evidence_cap(
                label=evidence_label,
                policy=policy,
            ),
            policy.maximum_strategy_allocation_percent,
        )

        eligible = (
            decision in {"continue", "promote"}
            and evidence_cap > 0
            and Decimal(
                str(ranking["capital_score"])
            )
            > 0
        )

        if not eligible:
            regime_multiplier = Decimal("0")
            regime_rationale = (
                "Strategy is ineligible under "
                "committee or evidence policy."
            )
        elif current_regime == "uncertain":
            regime_multiplier = Decimal("1.00")
            regime_rationale = (
                "Current regime is uncertain; "
                "no regime adjustment applied."
            )
        else:
            (
                regime_multiplier,
                regime_rationale,
            ) = _regime_adjustment(
                strategy_name=strategy_name,
                current_regime=current_regime,
                regime_results=regime_results,
                policy=policy,
            )

        adjusted_score = (
            Decimal(
                str(ranking["capital_score"])
            )
            * regime_multiplier
            if eligible
            else Decimal("0")
        )

        candidates.append(
            {
                "rank": ranking["rank"],
                "strategy_name": strategy_name,
                "committee_decision": decision,
                "next_action": _next_action(
                    committee_decision=decision,
                    evidence_label=evidence_label,
                ),
                "evidence_label": evidence_label,
                "evidence_cap_percent": (
                    evidence_cap
                ),
                "regime_multiplier": (
                    regime_multiplier
                ),
                "regime_rationale": (
                    regime_rationale
                ),
                "adjusted_score": (
                    adjusted_score
                ),
                "eligible": eligible,
            }
        )

    total_adjusted_score = sum(
        (
            candidate["adjusted_score"]
            for candidate in candidates
        ),
        Decimal("0"),
    )

    recommendations = []

    for candidate in candidates:
        if total_adjusted_score > 0:
            proportional_allocation = (
                policy
                .maximum_total_allocation_percent
                * candidate["adjusted_score"]
                / total_adjusted_score
            )
        else:
            proportional_allocation = Decimal("0")

        allocation_percent = min(
            proportional_allocation,
            candidate["evidence_cap_percent"],
        )

        allocation_percent = (
            allocation_percent.quantize(
                Decimal("0.01")
            )
        )

        allocation_usd = (
            policy.notional_capital_usd
            * allocation_percent
            / Decimal("100")
        ).quantize(
            Decimal("0.01")
        )

        recommendations.append(
            {
                **candidate,
                "evidence_cap_percent": float(
                    candidate[
                        "evidence_cap_percent"
                    ]
                ),
                "regime_multiplier": float(
                    candidate[
                        "regime_multiplier"
                    ]
                ),
                "adjusted_score": round(
                    float(
                        candidate[
                            "adjusted_score"
                        ]
                    ),
                    4,
                ),
                "recommended_allocation_percent": (
                    float(allocation_percent)
                ),
                "recommended_allocation_usd": (
                    float(allocation_usd)
                ),
            }
        )

    deployed_percent = sum(
        Decimal(
            str(
                recommendation[
                    "recommended_allocation_percent"
                ]
            )
        )
        for recommendation in recommendations
    )

    cash_percent = (
        Decimal("100") - deployed_percent
    ).quantize(
        Decimal("0.01")
    )

    cash_usd = (
        policy.notional_capital_usd
        * cash_percent
        / Decimal("100")
    ).quantize(
        Decimal("0.01")
    )

    return {
        "status": "success",
        "policy_name": policy.name,
        "mode": "shadow",
        "current_regime": current_regime,
        "notional_capital_usd": float(
            policy.notional_capital_usd
        ),
        "deployed_percent": float(
            deployed_percent
        ),
        "cash_reserve_percent": float(
            cash_percent
        ),
        "cash_reserve_usd": float(cash_usd),
        "recommendations": recommendations,
        "paper_portfolio_writes": False,
        "paper_execution_authority": False,
        "live_capital_authority": False,
    }


def get_shadow_allocation() -> dict[str, Any]:
    rankings = get_strategy_rankings()
    committee = get_capital_committee()
    market_regime = get_market_regime()
    regime_performance = (
        get_regime_performance()
    )

    current_regime = (
        market_regime["combined_regime"]
        if market_regime["status"] == "success"
        else "uncertain"
    )

    regime_results = (
        regime_performance["results"]
        if (
            regime_performance["status"]
            == "success"
        )
        else []
    )

    result = build_shadow_allocation(
        rankings=rankings["rankings"],
        committee_reports=committee["reports"],
        current_regime=current_regime,
        regime_results=regime_results,
    )

    result["input_status"] = {
        "rankings": rankings["status"],
        "committee": committee["status"],
        "market_regime": (
            market_regime["status"]
        ),
        "regime_performance": (
            regime_performance["status"]
        ),
    }

    return result
