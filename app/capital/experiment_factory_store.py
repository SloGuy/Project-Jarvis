"""Database schema for paper experiment factory requests.

Importing this module does not create tables or portfolios.
"""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.market_db.database import Base
from app.market_db.models import Portfolio  # Register the FK target.


def utc_now():
    return datetime.now(timezone.utc)


class ExperimentFactoryRecord(Base):
    __tablename__ = "capital_experiment_factory"
    __table_args__ = (
        CheckConstraint(
            "status IN ('awaiting_review', 'rejected', 'created')",
            name="ck_factory_status",
        ),
        CheckConstraint(
            """
            (
                status = 'created'
                AND portfolio_id IS NOT NULL
                AND approved_by IS NOT NULL
                AND approved_at IS NOT NULL
                AND approval_snapshot IS NOT NULL
                AND approval_sha256 IS NOT NULL
            )
            OR
            (
                status IN ('awaiting_review', 'rejected')
                AND portfolio_id IS NULL
                AND approved_by IS NULL
                AND approved_at IS NULL
                AND approval_snapshot IS NULL
                AND approval_sha256 IS NULL
            )
            """,
            name="ck_factory_creation_requires_approval",
        ),
    )

    request_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    research_id: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True
    )
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="awaiting_review"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now,
        onupdate=utc_now,
    )

    portfolio_id: Mapped[int | None] = mapped_column(
        ForeignKey("portfolios.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approval_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    approval_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(String(4000), nullable=True)
