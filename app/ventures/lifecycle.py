from __future__ import annotations

from app.ventures.models import OpportunityStatus


ALLOWED_TRANSITIONS: dict[
    OpportunityStatus,
    set[OpportunityStatus],
] = {
    OpportunityStatus.DISCOVERED: {
        OpportunityStatus.SCREENING,
        OpportunityStatus.REJECTED,
    },
    OpportunityStatus.SCREENING: {
        OpportunityStatus.RESEARCH,
        OpportunityStatus.WATCH,
        OpportunityStatus.REJECTED,
    },
    OpportunityStatus.RESEARCH: {
        OpportunityStatus.UNDERWRITING,
        OpportunityStatus.WATCH,
        OpportunityStatus.REJECTED,
    },
    OpportunityStatus.UNDERWRITING: {
        OpportunityStatus.COMMITTEE,
        OpportunityStatus.RESEARCH,
        OpportunityStatus.WATCH,
        OpportunityStatus.REJECTED,
    },
    OpportunityStatus.COMMITTEE: {
        OpportunityStatus.APPROVED,
        OpportunityStatus.RESEARCH,
        OpportunityStatus.UNDERWRITING,
        OpportunityStatus.WATCH,
        OpportunityStatus.REJECTED,
    },
    OpportunityStatus.WATCH: {
        OpportunityStatus.SCREENING,
        OpportunityStatus.RESEARCH,
        OpportunityStatus.REJECTED,
    },
    OpportunityStatus.REJECTED: set(),
    OpportunityStatus.APPROVED: set(),
}


def can_transition(
    current_status: OpportunityStatus,
    target_status: OpportunityStatus,
) -> bool:
    return target_status in ALLOWED_TRANSITIONS[current_status]


def require_valid_transition(
    current_status: OpportunityStatus,
    target_status: OpportunityStatus,
) -> None:
    if current_status == target_status:
        raise ValueError(
            f"Opportunity is already in status: "
            f"{current_status.value}"
        )

    if not can_transition(
        current_status,
        target_status,
    ):
        raise ValueError(
            "Invalid Ventures lifecycle transition: "
            f"{current_status.value} -> "
            f"{target_status.value}"
        )
