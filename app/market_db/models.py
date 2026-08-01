from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.market_db.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MarketAsset(Base):
    __tablename__ = "market_assets"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "asset_type",
            name="uq_market_assets_symbol_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str | None] = mapped_column(String(120))
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_id: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    observations: Mapped[list["PriceObservation"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
    )


class PriceObservation(Base):
    __tablename__ = "price_observations"
    __table_args__ = (
        Index(
            "ix_price_observations_asset_observed",
            "asset_id",
            "observed_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("market_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    price_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
    )
    change_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6),
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    asset: Mapped[MarketAsset] = relationship(
        back_populates="observations"
    )

class MarketAlert(Base):
    __tablename__ = "market_alerts"
    __table_args__ = (
        Index(
            "ix_market_alerts_symbol_created",
            "symbol",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    price_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
    )
    move_percent: Mapped[Decimal] = mapped_column(
        Numeric(12, 6),
        nullable=False,
    )
    comparison_minutes: Mapped[int] = mapped_column(nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

