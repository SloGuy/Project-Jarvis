import json
import time

from app.ventures import assessment_llm
from app.ventures.discovery_screening_store import list_research_queue
from app.ventures.opportunities import get_opportunity
from app.ventures.research_store import get_latest_research_report


def main():
    if assessment_llm.ASSESSMENT_MODEL != "qwen3:8b":
        raise ValueError("This preview requires qwen3:8b.")

    candidates = []
    for entry in list_research_queue():
        if entry["status"] != "collected":
            continue

        opportunity = get_opportunity(entry["opportunity_id"])
        if opportunity is None:
            continue
        if opportunity.business_type.value not in {"saas", "micro_saas"}:
            continue
        if opportunity.status.value not in {
            "discovered", "screening", "research"
        }:
            continue

        record = get_latest_research_report(opportunity.opportunity_id)
        if record is not None:
            candidates.append((opportunity, record))

    if not candidates:
        raise ValueError("No collected SaaS candidate is available.")

    # Choose a smaller report for the first bounded live check.
    opportunity, record = min(
        candidates,
        key=lambda pair: len(json.dumps(pair[1])),
    )

    print("Candidate:", opportunity.name, flush=True)
    print("Model:", assessment_llm.ASSESSMENT_MODEL, flush=True)
    started = time.monotonic()
    result = assessment_llm.assess_research_report(record)
    print("Elapsed seconds:", round(time.monotonic() - started, 1))
    print(json.dumps(result, indent=2))

    assessment = result["assessment"]
    cited = set(assessment["classification_claim_ids"])
    for finding in assessment["findings"]:
        cited.update(finding["claim_ids"])

    print("\nReferenced source claims:")
    for claim in record["report"]["claims"]:
        if claim["claim_id"] in cited:
            print(json.dumps(claim, indent=2))


if __name__ == "__main__":
    main()
