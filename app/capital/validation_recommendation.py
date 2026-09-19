"""Map assessment outcomes to advisory recommendations.

This function does not verify evidence or change research state.
Callers must verify the assessment before applying its recommendation.
"""
from app.capital.research_models import ResearchVerdict


def build_validation_recommendation(assessment):
    if assessment.get("promotion_authorized") is not False:
        raise ValueError("Assessment must explicitly deny promotion authority.")

    outcomes = {
        "pass": ("PASS", ResearchVerdict.PROMISING),
        "fail": ("REJECT", ResearchVerdict.UNPROMISING),
        "insufficient_evidence": ("REVISE", ResearchVerdict.INCONCLUSIVE),
    }
    status = assessment.get("validation_status")
    if not isinstance(status, str) or status not in outcomes:
        raise ValueError("Unknown or missing validation status.")

    verdict, research_verdict = outcomes[status]
    return {
        "schema_version": 1,
        "validation_status": status,
        "recommendation": verdict,
        "research_verdict": research_verdict.value,
        "promotion_authorized": False,
        "human_approval_required": True,
    }
