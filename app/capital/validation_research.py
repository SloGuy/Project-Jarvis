"""Verify completed validation receipts and attach their actual outcomes."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from app.capital.validation_registry import get_plan
from app.capital.validation_plan import research_snapshot
from app.capital.validation_assessment import assess_report
from app.capital.research_models import ResearchCandidate
from app.capital.research_store import locked_research_state, utc_now_iso


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def inspect_completed(plan_id):
    row = get_plan(plan_id)
    require(row["status"] == "completed", "Validation run is not completed.")
    receipt = json.loads(row["history"][-1]["detail"])
    require(receipt.get("acceptance_assessed") is True,
            "Completion has no recorded assessment.")
    require(receipt.get("promotion_authorized") is False,
            "Unexpected promotion authority.")
    directory = Path(receipt["directory"])
    result_raw = (directory / "result.json").read_bytes()
    assessment_raw = (directory / "assessment.json").read_bytes()
    require(digest(result_raw) == receipt["result_sha256"],
            "Completion result hash changed.")
    require(digest(assessment_raw) == receipt["assessment_sha256"],
            "Assessment hash changed.")
    result = json.loads(result_raw)
    saved_assessment = json.loads(assessment_raw)
    expected = {"plan_id": plan_id, "sha256": row["registered_sha256"]}
    require(
        result.get("status") == "completed"
        and result.get("designation") == "prospective_validation"
        and result.get("validation_registration") == expected
        and result.get("promotion_authorized") is False,
        "Result does not match the validation registration.",
    )
    names = ("plan.json", "report.json", "verification.json", "analysis.json")
    raw = {name: (directory / name).read_bytes() for name in names}
    require(
        result.get("artifacts_sha256")
        == {name: digest(value) for name, value in raw.items()},
        "Replay artifact hash changed.",
    )
    plan = row["envelope"]["plan"]
    report = json.loads(raw["report.json"])
    require(report.get("validation_registration") == expected,
            "Report references another registration.")
    recomputed = assess_report(plan, report)
    recomputed.update({
        "input_report_sha256": digest(raw["report.json"]),
        "input_result_sha256": digest(result_raw),
    })
    require(recomputed == saved_assessment,
            "Saved assessment differs from recomputation.")
    for name in ("criteria_status", "validation_status"):
        require(receipt.get(name) == recomputed[name],
                f"Completion receipt differs from assessment: {name}")
    require(recomputed.get("promotion_authorized") is False,
            "Unexpected assessment authority.")
    return {
        "plan_id": plan_id,
        "plan_sha256": row["registered_sha256"],
        "research": deepcopy(plan["research"]),
        "packet_directory": str(directory),
        "report_sha256": digest(raw["report.json"]),
        "assessment_sha256": digest(assessment_raw),
        "assessment": recomputed,
    }


def inspect_recommendation(plan_id):
    """Verify one completed attempt and return an advisory recommendation."""
    from app.capital.validation_recommendation import (
        build_validation_recommendation,
    )

    verified = inspect_completed(plan_id)
    research = verified["research"]

    with locked_research_state() as state:
        research_id = research["research_id"]
        if research_id not in state["candidates"]:
            raise KeyError(f"Unknown research candidate: {research_id}")
        candidate = ResearchCandidate.from_dict(
            state["candidates"][research_id]
        )
        require(
            research_snapshot(candidate) == research,
            "Research changed since validation registration.",
        )

    return {
        "plan_id": verified["plan_id"],
        "plan_sha256": verified["plan_sha256"],
        "research": deepcopy(research),
        "report_sha256": verified["report_sha256"],
        "assessment_sha256": verified["assessment_sha256"],
        "scope": "Single registered attempt; not aggregate promotion eligibility.",
        **build_validation_recommendation(verified["assessment"]),
    }


def record_recommendation(plan_id):
    """Persist a verified advisory recommendation without changing authority."""
    recommendation = inspect_recommendation(plan_id)
    research = recommendation["research"]

    with locked_research_state(write=True) as state:
        research_id = research["research_id"]
        if research_id not in state["candidates"]:
            raise KeyError(f"Unknown research candidate: {research_id}")

        candidate = ResearchCandidate.from_dict(
            state["candidates"][research_id]
        )
        require(
            research_snapshot(candidate) == research,
            "Research changed before recommendation persistence.",
        )

        existing = [
            item for item in candidate.validation_recommendations
            if item.get("plan_id") == plan_id
        ]
        require(len(existing) <= 1, "Duplicate recommendation records.")
        if existing:
            saved = {
                key: value for key, value in existing[0].items()
                if key != "recorded_at"
            }
            require(
                saved == recommendation,
                "Existing recommendation differs from verified evidence.",
            )
            return deepcopy(existing[0])

        record = {
            **deepcopy(recommendation),
            "recorded_at": utc_now_iso(),
        }
        candidate.validation_recommendations.append(record)
        candidate.updated_at = record["recorded_at"]
        state["candidates"][research_id] = candidate.to_dict()

    return deepcopy(record)


def attach_completed(plan_id):
    verified = inspect_completed(plan_id)
    research = verified["research"]
    with locked_research_state(write=True) as state:
        research_id = research["research_id"]
        if research_id not in state["candidates"]:
            raise KeyError(f"Unknown research candidate: {research_id}")
        candidate = ResearchCandidate.from_dict(state["candidates"][research_id])
        require(research_snapshot(candidate) == research,
                "Research changed since validation registration.")
        for existing in candidate.validation_assessments:
            if existing["plan_id"] == plan_id:
                require(
                    all(existing.get(key) == value for key, value in verified.items()),
                    "An existing attachment differs from this assessment.",
                )
                return deepcopy(existing)
        record = {**verified, "attached_at": utc_now_iso()}
        candidate.validation_assessments.append(record)
        candidate.updated_at = record["attached_at"]
        state["candidates"][research_id] = candidate.to_dict()
    return deepcopy(record)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("plan_id")
    parser.add_argument("--attach", action="store_true")
    args = parser.parse_args()
    result = (
        attach_completed(args.plan_id) if args.attach
        else inspect_completed(args.plan_id)
    )
    print(json.dumps(result, indent=2))
    print("ATTACHED" if args.attach else "VERIFIED: research state unchanged.")


if __name__ == "__main__":
    main()
