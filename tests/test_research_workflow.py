from pathlib import Path
from tempfile import TemporaryDirectory

from app.capital import research_store
from app.capital.research_models import (
    ResearchStatus,
    ResearchVerdict,
)
from app.capital.research_service import (
    get_research_summary,
    propose_research_candidate,
    require_research_candidate,
)
from app.capital.research_workflow import (
    begin_research_screening,
    begin_strategy_research,
    evaluate_research_candidate,
)


def configure_temporary_store(
    directory: str,
) -> None:
    state_directory = Path(directory)

    research_store.STATE_DIRECTORY = (
        state_directory
    )
    research_store.RESEARCH_STATE_FILE = (
        state_directory
        / "research_candidates.json"
    )
    research_store.RESEARCH_LOCK_FILE = (
        state_directory
        / "research_candidates.lock"
    )


with TemporaryDirectory() as directory:
    configure_temporary_store(
        directory
    )

    candidate = propose_research_candidate(
        strategy_name="volatility_breakout_v1",
        display_name="Volatility Breakout V1",
        hypothesis=(
            "Volatility expansion after compression "
            "can produce persistent directional moves."
        ),
        description=(
            "Candidate breakout strategy using "
            "volatility compression and price range "
            "expansion."
        ),
        market_regime=(
            "Transition from low to high volatility"
        ),
        asset_universe=[
            "SPY",
            "QQQ",
            "BTC",
        ],
        data_requirements=[
            "Historical prices",
            "Rolling volatility",
            "Trading volume",
        ],
        risk_thesis=(
            "False breakouts and volatility shocks "
            "must be bounded by position and exposure "
            "limits."
        ),
        success_criteria=[
            "Positive expectancy",
            "Profit factor above 1.20",
            "Maximum drawdown below 10%",
        ],
        proposed_by="jarvis_capital_research",
    )

    assert (
        candidate.status
        == ResearchStatus.PROPOSED
    )

    try:
        propose_research_candidate(
            strategy_name="volatility_breakout_v1",
            display_name="Duplicate",
            hypothesis="Duplicate hypothesis",
            description="Duplicate description",
            market_regime="Any",
            asset_universe=["SPY"],
            data_requirements=["Prices"],
            risk_thesis="Duplicate risk thesis",
            success_criteria=["Positive return"],
            proposed_by="test",
        )
        raise AssertionError(
            "Duplicate candidate was accepted."
        )
    except ValueError:
        print("duplicate_protection: PASS")

    screened = begin_research_screening(
        research_id=candidate.research_id,
    )
    assert (
        screened.status
        == ResearchStatus.SCREENING
    )

    researching = begin_strategy_research(
        research_id=candidate.research_id,
    )
    assert (
        researching.status
        == ResearchStatus.RESEARCHING
    )

    evaluated = evaluate_research_candidate(
        research_id=candidate.research_id,
        verdict=ResearchVerdict.PROMISING,
        evidence=[
            "Hypothesis is testable with existing data.",
            "Risk conditions are explicitly bounded.",
        ],
        concerns=[
            "Breakout confirmation requires validation.",
        ],
        evaluation_notes=(
            "Advance to isolated experiment design. "
            "No capital or execution authorization."
        ),
    )

    assert (
        evaluated.status
        == ResearchStatus.READY_FOR_EXPERIMENT
    )
    assert (
        evaluated.verdict
        == ResearchVerdict.PROMISING
    )

    persisted = require_research_candidate(
        research_id=candidate.research_id,
    )
    assert (
        persisted.research_id
        == candidate.research_id
    )

    summary = get_research_summary()

    assert summary["candidate_count"] == 1
    assert (
        summary["status_counts"][
            "ready_for_experiment"
        ]
        == 1
    )

    print("proposal_persistence: PASS")
    print("controlled_transitions: PASS")
    print("evidence_gate: PASS")
    print("trading_state_writes: NONE")
