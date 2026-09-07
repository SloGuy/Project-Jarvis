from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.market_db.database import Base


class ExperimentCycle(Base):
    __tablename__ = "capital_experiment_cycles"
    __table_args__ = (
        Index(
            "ix_capital_cycles_experiment_mode_started",
            "experiment_id",
            "mode",
            "started_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    experiment_id: Mapped[str] = mapped_column(
        String(120), nullable=False,
    )
    mode: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running",
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict,
    )
    error: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
