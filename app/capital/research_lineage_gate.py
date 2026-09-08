"""Research lineage requirement for Committee graduation."""
from app.capital.committee_models import GateStatus, GraduationGate


def build_research_lineage_gate(provenance):
    matched = provenance.get("status") == "matched"
    reasons = provenance.get("reasons") or [
        "Research lineage has not been verified."
    ]
    return GraduationGate(
        gate_name="research_lineage",
        status=GateStatus.PASSED if matched else GateStatus.PENDING,
        actual_value=provenance.get("status", "unknown"),
        required_value="matched",
        rationale=(
            "Current research and registered strategy lineage match. "
            "This does not verify launch configuration or historical validation."
            if matched else
            "Promotion is blocked pending research lineage resolution: "
            + "; ".join(str(reason) for reason in reasons)
        ),
    )
