"""Persistent lifecycle state for factory-created paper experiments.

Defining this model does not create tables or activate portfolios.
Lifecycle changes must be authorized and committed with their history.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.market_db.database import Base
from app.capital.experiment_factory_store import ExperimentFactoryRecord


def utc_now():
    return datetime.now(timezone.utc)


class PaperLifecycleRecord(Base):
    __tablename__ = "capital_paper_lifecycle"
    __table_args__ = (
        CheckConstraint(
            "status IN "
            "('planned', 'active', 'paused', 'demoted', 'retired')",
            name="ck_paper_lifecycle_status",
        ),
        CheckConstraint(
            "execution_mode = 'paper'",
            name="ck_paper_lifecycle_paper_only",
        ),
        CheckConstraint(
            "allocation_usd >= 0 "
            "AND allocation_usd < 1000000000000",
            name="ck_paper_lifecycle_allocation",
        ),
        CheckConstraint(
            "status = 'active' OR allocation_usd = 0",
            name="ck_paper_lifecycle_inactive_allocation",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_paper_lifecycle_version",
        ),
    )

    request_key: Mapped[str] = mapped_column(
        String(100),
        ForeignKey(
            "capital_experiment_factory.request_key",
            ondelete="RESTRICT",
        ),
        primary_key=True,
    )
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="planned",
    )
    execution_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="paper",
    )

    # Entry allocation is a limit, not a deposit or portfolio valuation.
    # Zero allocation must not prevent closing existing positions.
    allocation_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
        default=Decimal("0"),
    )
    policy_version: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # The service must replace JSON values rather than mutate them in place.
    authorization_snapshot: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )
    transition_history: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    # Detect stale ORM updates. Services must also lock rows when deciding
    # transitions and serialize allocation decisions across portfolios.
    __mapper_args__ = {"version_id_col": version}
