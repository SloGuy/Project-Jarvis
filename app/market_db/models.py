from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    JSON,
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
    news_links: Mapped[list["MarketNewsArticleAsset"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
    )
    portfolio_positions: Mapped[list["PortfolioPosition"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
    )
    portfolio_transactions: Mapped[
        list["PortfolioTransaction"]
    ] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
    )


class PriceObservation(Base):
    __tablename__ = "price_observations"
    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "provider",
            "observed_at",
            name="uq_price_observations_asset_provider_time",
        ),
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


class MarketNewsArticle(Base):
    __tablename__ = "market_news_articles"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_article_id",
            name="uq_market_news_provider_article",
        ),
        UniqueConstraint(
            "content_hash",
            name="uq_market_news_content_hash",
        ),
        Index(
            "ix_market_news_published_at",
            "published_at",
        ),
        Index(
            "ix_market_news_provider_published",
            "provider",
            "published_at",
        ),
        Index(
            "ix_market_news_type_published",
            "article_type",
            "published_at",
        ),
        Index(
            "ix_market_news_processed_published",
            "processed",
            "published_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    provider_article_id: Mapped[str | None] = mapped_column(
        String(255),
    )
    title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    summary: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    image_url: Mapped[str | None] = mapped_column(Text)
    source_name: Mapped[str | None] = mapped_column(
        String(255),
    )
    author: Mapped[str | None] = mapped_column(
        String(255),
    )
    article_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    sentiment_label: Mapped[str | None] = mapped_column(
        String(20),
    )
    sentiment_score: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 6),
    )
    importance_score: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 6),
    )
    processed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    processing_error: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    raw_payload: Mapped[dict | None] = mapped_column(JSON)

    asset_links: Mapped[list["MarketNewsArticleAsset"]] = relationship(
        back_populates="article",
        cascade="all, delete-orphan",
    )


class MarketNewsArticleAsset(Base):
    __tablename__ = "market_news_article_assets"
    __table_args__ = (
        UniqueConstraint(
            "article_id",
            "asset_id",
            name="uq_market_news_article_asset",
        ),
        Index(
            "ix_market_news_article_assets_asset_article",
            "asset_id",
            "article_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey(
            "market_news_articles.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey(
            "market_assets.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    link_type: Mapped[str] = mapped_column(
        String(30),
        default="legacy",
        nullable=False,
    )
    linked_by: Mapped[str] = mapped_column(
        String(30),
        default="legacy",
        nullable=False,
    )
    match_reason: Mapped[str | None] = mapped_column(Text)
    matched_text: Mapped[str | None] = mapped_column(Text)
    confidence_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        default=Decimal("0.5000"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    article: Mapped["MarketNewsArticle"] = relationship(
        back_populates="asset_links",
    )
    asset: Mapped["MarketAsset"] = relationship(
        back_populates="news_links",
    )


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="Primary Portfolio",
    )
    portfolio_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="paper",
    )
    cash_balance_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
        default=Decimal("100000.00"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    positions: Mapped[list["PortfolioPosition"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )
    transactions: Mapped[list["PortfolioTransaction"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )


class PortfolioPosition(Base):
    __tablename__ = "portfolio_positions"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "asset_id",
            name="uq_portfolio_positions_portfolio_asset",
        ),
        Index(
            "ix_portfolio_positions_portfolio_asset",
            "portfolio_id",
            "asset_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("market_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(28, 12),
        nullable=False,
        default=Decimal("0"),
    )
    average_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
        default=Decimal("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    portfolio: Mapped["Portfolio"] = relationship(
        back_populates="positions",
    )
    asset: Mapped["MarketAsset"] = relationship(
        back_populates="portfolio_positions",
    )


class PortfolioTransaction(Base):
    __tablename__ = "portfolio_transactions"
    __table_args__ = (
        Index(
            "ix_portfolio_transactions_portfolio_created",
            "portfolio_id",
            "created_at",
        ),
        Index(
            "ix_portfolio_transactions_asset_created",
            "asset_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_assets.id", ondelete="CASCADE"),
        nullable=True,
    )
    transaction_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(28, 12),
        nullable=False,
    )
    price_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
    )
    total_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
    )
    fees_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
        default=Decimal("0"),
    )
    realized_gain_loss_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 8),
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    portfolio: Mapped["Portfolio"] = relationship(
        back_populates="transactions",
    )
    asset: Mapped["MarketAsset | None"] = relationship(
        back_populates="portfolio_transactions",
    )


class AutonomousTradeDecision(Base):
    __tablename__ = "autonomous_trade_decisions"
    __table_args__ = (
        Index(
            "ix_autonomous_trade_decisions_portfolio_created",
            "portfolio_id",
            "created_at",
        ),
        Index(
            "ix_autonomous_trade_decisions_asset_created",
            "asset_id",
            "created_at",
        ),
        Index(
            "ix_autonomous_trade_decisions_approved_created",
            "approved",
            "created_at",
        ),
        Index(
            "ix_autonomous_trade_decisions_execution_status",
            "execution_status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False,
    )

    asset_id: Mapped[int] = mapped_column(
        ForeignKey("market_assets.id", ondelete="CASCADE"),
        nullable=False,
    )

    policy_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    strategy_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    action: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(28, 12),
        nullable=False,
    )

    reference_price_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
    )

    price_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    confidence_percent: Mapped[Decimal] = mapped_column(
        Numeric(8, 4),
        nullable=False,
    )

    rationale: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    approved: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )

    rejection_reasons: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    execution_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="not_executed",
    )

    execution_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    portfolio_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "portfolio_transactions.id",
            ondelete="SET NULL",
        ),
    )

    execution_error: Mapped[str | None] = mapped_column(
        Text,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class AutonomousStrategyState(Base):
    __tablename__ = "autonomous_strategy_state"
    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "strategy_name",
            name=(
                "uq_autonomous_strategy_state_"
                "asset_strategy"
            ),
        ),
        Index(
            "ix_autonomous_strategy_state_strategy_action",
            "strategy_name",
            "pending_action",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    asset_id: Mapped[int] = mapped_column(
        ForeignKey(
            "market_assets.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    strategy_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    pending_action: Mapped[str | None] = mapped_column(
        String(20),
    )

    confirmation_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
    )

    first_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    last_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    last_observation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
