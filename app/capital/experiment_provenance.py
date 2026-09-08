from app.capital.research_models import ResearchStatus, ResearchVerdict


def assess_provenance(experiment, candidate, strategy) -> dict:
    if experiment.research_id is None:
        return {
            "status": "unlinked",
            "reasons": ["No research lineage recorded."],
            "launch_snapshot_verified": False,
        }

    reasons = []
    if candidate is None:
        reasons.append("Linked research candidate does not exist.")
    else:
        if candidate.research_id != experiment.research_id:
            reasons.append("Research ID mismatch.")
        if candidate.strategy_name != experiment.strategy_name:
            reasons.append("Research strategy name mismatch.")
        if candidate.hypothesis_version != experiment.hypothesis_version:
            reasons.append("Hypothesis version mismatch.")
        if (
            candidate.status not in {
                ResearchStatus.READY_FOR_EXPERIMENT,
                ResearchStatus.ARCHIVED,
            }
            or candidate.verdict != ResearchVerdict.PROMISING
        ):
            reasons.append("Research is not currently experiment-ready.")

    if strategy is None:
        reasons.append("Registered strategy does not exist.")
    else:
        if strategy.name != experiment.strategy_name:
            reasons.append("Registered strategy name mismatch.")
        if strategy.version != experiment.strategy_version:
            reasons.append("Registered strategy version mismatch.")

    return {
        "status": "mismatch" if reasons else "matched",
        "reasons": reasons,
        "launch_snapshot_verified": False,
    }


def get_experiment_provenance(experiment) -> dict:
    if experiment.research_id is None:
        return assess_provenance(experiment, None, None)

    from app.capital.research_service import get_research_candidate
    from app.capital.strategy_registry import get_strategy

    candidate = get_research_candidate(
        research_id=experiment.research_id
    )
    strategy = get_strategy(strategy_name=experiment.strategy_name)
    return assess_provenance(experiment, candidate, strategy)
