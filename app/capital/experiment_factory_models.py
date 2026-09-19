"""Agent submission contract for the paper experiment factory.

Submission is not approval or permission to execute trades.
The server resolves strategy, policy, and validation evidence.
"""
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class FactoryStatus(str, Enum):
    AWAITING_REVIEW = "awaiting_review"
    REJECTED = "rejected"
    CREATED = "created"


class ExperimentFactoryRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )

    request_key: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    research_id: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^research_[A-Za-z0-9_-]+$",
    )
    requested_by: str = Field(
        min_length=1,
        max_length=120,
        description=(
            "Attribution supplied by the requester. "
            "Not an authenticated identity or approval credential."
        ),
    )
