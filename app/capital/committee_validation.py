"""Committee gate backed by registered, verified validation attempts."""
from app.capital.committee_models import GraduationGate, GateStatus
from app.capital.research_service import get_research_candidate
from app.capital.validation_plan import research_snapshot
from app.capital.validation_registry import locked_state
from app.capital.validation_research import inspect_completed


def gate(status, reasons, attempts):
    return GraduationGate(
        gate_name="registered_validation",
        status=status,
        actual_value={"attempts": attempts, "reasons": reasons},
        required_value="All current registered attempts attached and verified as passing",
        rationale="; ".join(reasons),
    )


def build_validation_gate(experiment):
    try:
        return _assess(experiment)
    except Exception as error:
        # Unavailable or damaged evidence is not a measured strategy failure.
        return gate(
            GateStatus.PENDING,
            [f"Validation evidence cannot be verified: {type(error).__name__}: {error}"],
            [],
        )


def _assess(experiment):
    if experiment.research_id is None:
        return gate(GateStatus.PENDING, ["Experiment has no research lineage."], [])
    candidate = get_research_candidate(research_id=experiment.research_id)
    if candidate is None:
        return gate(GateStatus.PENDING, ["Linked research candidate is missing."], [])
    if (
        candidate.hypothesis_version != experiment.hypothesis_version
        or candidate.strategy_name != experiment.strategy_name
    ):
        return gate(GateStatus.PENDING, ["Experiment and research bindings differ."], [])

    research = research_snapshot(candidate)
    with locked_state() as state:
        # Include failed and unfinished attempts; do not select only good results.
        attempts = [
            (plan_id, row["status"])
            for plan_id, row in state["plans"].items()
            if (
                row["envelope"]["plan"]["research"]["research_id"]
                == candidate.research_id
                and row["envelope"]["plan"]["research"]["hypothesis_version"]
                == candidate.hypothesis_version
                and row["envelope"]["plan"]["strategy_version"]
                == experiment.strategy_version
            )
        ]
    if not attempts:
        return gate(GateStatus.PENDING, ["No registered validation attempts exist."], [])

    attachments = {}
    for item in candidate.validation_assessments:
        if item["plan_id"] in attachments:
            raise ValueError("Duplicate validation attachment.")
        attachments[item["plan_id"]] = item

    reasons, outcomes = [], []
    measured_failure = False
    for plan_id, status in sorted(attempts):
        outcome = {"plan_id": plan_id, "run_status": status}
        outcomes.append(outcome)
        if status != "completed":
            reasons.append(f"{plan_id}: run is {status}; evidence remains incomplete.")
            continue
        attached = attachments.get(plan_id)
        if attached is None:
            reasons.append(f"{plan_id}: assessment has not been attached to research.")
            continue
        try:
            verified = inspect_completed(plan_id)
            if verified["research"] != research:
                raise ValueError("Registered research differs from current research.")
            for key in (
                "plan_sha256", "report_sha256", "assessment_sha256", "assessment",
            ):
                if attached.get(key) != verified[key]:
                    raise ValueError(f"Attachment differs from verified evidence: {key}")
            assessment = verified["assessment"]
            result = assessment["validation_status"]
            if result not in {"pass", "fail", "insufficient_evidence"}:
                raise ValueError("Unknown validation assessment outcome.")
            if assessment.get("promotion_authorized") is not False:
                raise ValueError("Unexpected promotion authority in assessment.")
            outcome["validation_status"] = result
            if result == "fail":
                measured_failure = True
                reasons.append(f"{plan_id}: verified validation criteria failed.")
            elif result == "insufficient_evidence":
                reasons.append(f"{plan_id}: validation evidence is insufficient.")
        except Exception as error:
            outcome["verification_error"] = str(error)
            reasons.append(f"{plan_id}: evidence could not be verified: {error}")

    if measured_failure:
        return gate(GateStatus.FAILED, reasons, outcomes)
    if reasons:
        return gate(GateStatus.PENDING, reasons, outcomes)
    return gate(
        GateStatus.PASSED,
        ["All current registered attempts have verified passing assessments. "
         "Separate paper, risk, and human-review requirements still apply."],
        outcomes,
    )
