import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

STATE_DIRECTORY = (
    PROJECT_ROOT
    / "runtime"
    / "agents"
)

APPROVAL_STATE_FILE = (
    STATE_DIRECTORY
    / "approvals.json"
)


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass
class ApprovalRequest:
    approval_id: str
    task_id: str
    agent_id: str
    action: str
    description: str
    payload: dict
    status: ApprovalStatus
    created_at: str
    decided_at: str | None = None
    decision_reason: str | None = None


def utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _load_state() -> dict:
    try:
        with APPROVAL_STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as handle:
            payload = json.load(
                handle
            )

        if isinstance(payload, dict):
            return payload

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        pass

    return {
        "approvals": {},
    }


def _save_state(
    state: dict,
) -> None:
    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    state["updated_at"] = (
        utc_now_iso()
    )

    with APPROVAL_STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            state,
            handle,
            indent=2,
        )


def _approval_to_record(
    approval: ApprovalRequest,
) -> dict:
    record = asdict(
        approval
    )

    record["status"] = (
        approval.status.value
    )

    return record


def _approval_from_record(
    record: dict,
) -> ApprovalRequest:
    return ApprovalRequest(
        approval_id=record[
            "approval_id"
        ],
        task_id=record[
            "task_id"
        ],
        agent_id=record[
            "agent_id"
        ],
        action=record[
            "action"
        ],
        description=record[
            "description"
        ],
        payload=dict(
            record.get(
                "payload",
                {},
            )
        ),
        status=ApprovalStatus(
            record.get(
                "status",
                ApprovalStatus.PENDING.value,
            )
        ),
        created_at=record[
            "created_at"
        ],
        decided_at=record.get(
            "decided_at"
        ),
        decision_reason=record.get(
            "decision_reason"
        ),
    )


def create_approval_request(
    *,
    task_id: str,
    agent_id: str,
    action: str,
    description: str,
    payload: dict | None = None,
) -> ApprovalRequest:
    normalized_action = (
        action.strip()
    )

    normalized_description = (
        description.strip()
    )

    if not task_id.strip():
        raise ValueError(
            "task_id is required"
        )

    if not agent_id.strip():
        raise ValueError(
            "agent_id is required"
        )

    if not normalized_action:
        raise ValueError(
            "action is required"
        )

    if not normalized_description:
        raise ValueError(
            "description is required"
        )

    approval = ApprovalRequest(
        approval_id=(
            "approval_"
            + uuid.uuid4().hex[:12]
        ),
        task_id=task_id.strip(),
        agent_id=agent_id.strip(),
        action=normalized_action,
        description=(
            normalized_description
        ),
        payload=dict(
            payload or {}
        ),
        status=ApprovalStatus.PENDING,
        created_at=utc_now_iso(),
    )

    state = _load_state()

    approvals = state.setdefault(
        "approvals",
        {},
    )

    approvals[
        approval.approval_id
    ] = _approval_to_record(
        approval
    )

    _save_state(
        state
    )

    return approval


def get_approval(
    approval_id: str,
) -> ApprovalRequest | None:
    state = _load_state()

    record = (
        state
        .get(
            "approvals",
            {},
        )
        .get(
            approval_id
        )
    )

    if record is None:
        return None

    return _approval_from_record(
        record
    )


def get_approvals(
    *,
    status: ApprovalStatus | None = None,
) -> tuple[
    ApprovalRequest,
    ...,
]:
    state = _load_state()

    records = list(
        state
        .get(
            "approvals",
            {},
        )
        .values()
    )

    approvals = [
        _approval_from_record(
            record
        )
        for record in records
    ]

    if status is not None:
        approvals = [
            approval
            for approval in approvals
            if approval.status == status
        ]

    approvals.sort(
        key=lambda approval: (
            approval.created_at
        ),
        reverse=True,
    )

    return tuple(
        approvals
    )


def decide_approval(
    *,
    approval_id: str,
    status: ApprovalStatus,
    reason: str | None = None,
) -> ApprovalRequest:
    if status not in {
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.CANCELLED,
    }:
        raise ValueError(
            "Approval decision must be approved, "
            "rejected, or cancelled."
        )

    state = _load_state()

    approvals = state.setdefault(
        "approvals",
        {},
    )

    record = approvals.get(
        approval_id
    )

    if record is None:
        raise ValueError(
            f"Approval not found: {approval_id}"
        )

    approval = _approval_from_record(
        record
    )

    if (
        approval.status
        != ApprovalStatus.PENDING
    ):
        raise ValueError(
            "Only pending approvals can be decided."
        )

    approval.status = status

    approval.decided_at = (
        utc_now_iso()
    )

    approval.decision_reason = (
        reason.strip()
        if reason
        else None
    )

    approvals[
        approval.approval_id
    ] = _approval_to_record(
        approval
    )

    _save_state(
        state
    )

    return approval


def approve_request(
    *,
    approval_id: str,
    reason: str | None = None,
) -> ApprovalRequest:
    return decide_approval(
        approval_id=approval_id,
        status=ApprovalStatus.APPROVED,
        reason=reason,
    )


def reject_request(
    *,
    approval_id: str,
    reason: str | None = None,
) -> ApprovalRequest:
    return decide_approval(
        approval_id=approval_id,
        status=ApprovalStatus.REJECTED,
        reason=reason,
    )


def get_approval_snapshot() -> dict:
    approvals = get_approvals()

    status_counts = {
        status.value: 0
        for status in ApprovalStatus
    }

    for approval in approvals:
        status_counts[
            approval.status.value
        ] += 1

    return {
        "status": "success",
        "approval_count": len(
            approvals
        ),
        "status_counts": (
            status_counts
        ),
        "approvals": [
            _approval_to_record(
                approval
            )
            for approval in approvals
        ],
    }


if __name__ == "__main__":
    print(
        json.dumps(
            get_approval_snapshot(),
            indent=2,
        )
    )
