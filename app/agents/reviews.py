import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from app.agents.patches import get_patch
from app.agents.registry import get_agent


PROJECT_ROOT = Path(__file__).resolve().parents[2]

STATE_DIRECTORY = (
    PROJECT_ROOT
    / "runtime"
    / "agents"
)

REVIEW_STATE_FILE = (
    STATE_DIRECTORY
    / "reviews.json"
)


class ReviewDecision(str, Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass
class PatchReview:
    review_id: str
    patch_id: str
    task_id: str
    reviewer_agent_id: str
    decision: ReviewDecision
    summary: str
    created_at: str


def utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _load_state() -> dict:
    try:
        with REVIEW_STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as handle:
            payload = json.load(handle)

        if isinstance(payload, dict):
            return payload

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        pass

    return {
        "reviews": {},
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

    with REVIEW_STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            state,
            handle,
            indent=2,
        )


def _review_from_record(
    record: dict,
) -> PatchReview:
    return PatchReview(
        review_id=record["review_id"],
        patch_id=record["patch_id"],
        task_id=record["task_id"],
        reviewer_agent_id=record[
            "reviewer_agent_id"
        ],
        decision=ReviewDecision(
            record["decision"]
        ),
        summary=record["summary"],
        created_at=record["created_at"],
    )


def create_patch_review(
    *,
    patch_id: str,
    reviewer_agent_id: str,
    decision: ReviewDecision,
    summary: str,
) -> PatchReview:
    patch = get_patch(
        patch_id
    )

    if patch is None:
        raise ValueError(
            f"Patch not found: {patch_id}"
        )

    reviewer = get_agent(
        reviewer_agent_id
    )

    if reviewer is None:
        raise ValueError(
            "Reviewer agent does not exist: "
            f"{reviewer_agent_id}"
        )

    if reviewer.role != "reviewer":
        raise ValueError(
            "Agent is not authorized as a reviewer."
        )

    normalized_summary = (
        summary.strip()
    )

    if not normalized_summary:
        raise ValueError(
            "review summary is required"
        )

    review = PatchReview(
        review_id=(
            "review_"
            + uuid.uuid4().hex[:12]
        ),
        patch_id=patch.patch_id,
        task_id=patch.task_id,
        reviewer_agent_id=(
            reviewer_agent_id
        ),
        decision=decision,
        summary=normalized_summary,
        created_at=utc_now_iso(),
    )

    state = _load_state()

    reviews = state.setdefault(
        "reviews",
        {},
    )

    reviews[
        review.review_id
    ] = asdict(
        review
    )

    reviews[
        review.review_id
    ]["decision"] = (
        review.decision.value
    )

    _save_state(
        state
    )

    return review


def get_review(
    review_id: str,
) -> PatchReview | None:
    state = _load_state()

    record = (
        state
        .get(
            "reviews",
            {},
        )
        .get(
            review_id
        )
    )

    if record is None:
        return None

    return _review_from_record(
        record
    )


def get_reviews_for_patch(
    patch_id: str,
) -> tuple[
    PatchReview,
    ...,
]:
    state = _load_state()

    reviews = [
        _review_from_record(
            record
        )
        for record in (
            state
            .get(
                "reviews",
                {},
            )
            .values()
        )
        if (
            record.get(
                "patch_id"
            )
            == patch_id
        )
    ]

    reviews.sort(
        key=lambda review: (
            review.created_at
        ),
        reverse=True,
    )

    return tuple(
        reviews
    )


def latest_patch_review(
    patch_id: str,
) -> PatchReview | None:
    reviews = (
        get_reviews_for_patch(
            patch_id
        )
    )

    if not reviews:
        return None

    return reviews[0]


def patch_has_passing_review(
    patch_id: str,
) -> bool:
    review = latest_patch_review(
        patch_id
    )

    return bool(
        review
        and review.decision
        == ReviewDecision.PASS
    )


def get_review_snapshot() -> dict:
    state = _load_state()

    reviews = [
        _review_from_record(
            record
        )
        for record in (
            state
            .get(
                "reviews",
                {},
            )
            .values()
        )
    ]

    reviews.sort(
        key=lambda review: (
            review.created_at
        ),
        reverse=True,
    )

    pass_count = sum(
        1
        for review in reviews
        if (
            review.decision
            == ReviewDecision.PASS
        )
    )

    fail_count = sum(
        1
        for review in reviews
        if (
            review.decision
            == ReviewDecision.FAIL
        )
    )

    return {
        "status": "success",
        "review_count": len(
            reviews
        ),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "reviews": [
            {
                "review_id": review.review_id,
                "patch_id": review.patch_id,
                "task_id": review.task_id,
                "reviewer_agent_id": (
                    review.reviewer_agent_id
                ),
                "decision": (
                    review.decision.value
                ),
                "summary": review.summary,
                "created_at": (
                    review.created_at
                ),
            }
            for review in reviews
        ],
    }


if __name__ == "__main__":
    print(
        json.dumps(
            get_review_snapshot(),
            indent=2,
        )
    )
