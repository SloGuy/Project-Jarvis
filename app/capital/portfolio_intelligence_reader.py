"""Consistent, read-only PostgreSQL inputs for portfolio diagnostics.

Returns raw records, not verified valuations or trading recommendations.
No default portfolio creation or live quote lookup is performed.
"""

from sqlalchemy import select, text

from app.capital.portfolio_daily_returns import utc_timestamp


def _portfolio_ids(values):
    identifiers = list(values)
    if not identifiers:
        raise ValueError("At least one portfolio ID is required.")
    if any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        for value in identifiers
    ):
        raise ValueError("Portfolio IDs must be positive integers.")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate portfolio IDs.")
    return sorted(identifiers)


def _rows(connection, statement):
    return [
        dict(row)
        for row in connection.execute(statement).mappings()
    ]


def read_portfolio_inputs(
    *,
    portfolio_ids,
    quote_window_start,
    database_engine=None,
):
    """Read a consistent snapshot for explicitly selected paper portfolios.

    quote_window_start must include the required pricing lookback before
    the first measurement. All surviving portfolio transactions are read.
    Missing price coverage must be handled as unavailable downstream.
    """
    identifiers = _portfolio_ids(portfolio_ids)
    quote_start = utc_timestamp(quote_window_start)

    from app.market_db.models import (
        MarketAsset,
        Portfolio,
        PortfolioPosition,
        PortfolioTransaction,
        PriceObservation,
    )

    if database_engine is None:
        from app.market_db.database import engine

        database_engine = engine

    if database_engine.dialect.name != "postgresql":
        raise ValueError(
            "This reader requires PostgreSQL snapshot isolation."
        )

    with database_engine.connect() as connection:
        connection = connection.execution_options(
            isolation_level="REPEATABLE READ"
        )
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            snapshot_at = utc_timestamp(
                connection.scalar(text("SELECT statement_timestamp()"))
            )
            if quote_start > snapshot_at:
                raise ValueError("Quote window starts after the snapshot.")

            portfolios = _rows(
                connection,
                select(Portfolio.__table__)
                .where(Portfolio.id.in_(identifiers))
                .order_by(Portfolio.id),
            )
            if len(portfolios) != len(identifiers):
                raise ValueError("One or more portfolios were not found.")
            if any(row["portfolio_type"] != "paper" for row in portfolios):
                raise ValueError("Only paper portfolios are supported.")

            positions = _rows(
                connection,
                select(PortfolioPosition.__table__)
                .where(PortfolioPosition.portfolio_id.in_(identifiers))
                .order_by(
                    PortfolioPosition.portfolio_id,
                    PortfolioPosition.asset_id,
                ),
            )
            transactions = _rows(
                connection,
                select(PortfolioTransaction.__table__)
                .where(PortfolioTransaction.portfolio_id.in_(identifiers))
                .order_by(
                    PortfolioTransaction.created_at,
                    PortfolioTransaction.id,
                ),
            )

            if any(
                utc_timestamp(row["created_at"]) > snapshot_at
                for row in transactions
            ):
                raise ValueError(
                    "Future-dated transactions prevent reconstruction."
                )

            asset_ids = sorted({
                row["asset_id"]
                for row in positions + transactions
                if row["asset_id"] is not None
            })
            assets = []
            observations = []

            if asset_ids:
                assets = _rows(
                    connection,
                    select(MarketAsset.__table__)
                    .where(MarketAsset.id.in_(asset_ids))
                    .order_by(MarketAsset.id),
                )
                observations = _rows(
                    connection,
                    select(PriceObservation.__table__)
                    .where(
                        PriceObservation.asset_id.in_(asset_ids),
                        PriceObservation.observed_at >= quote_start,
                        PriceObservation.observed_at <= snapshot_at,
                    )
                    .order_by(
                        PriceObservation.asset_id,
                        PriceObservation.observed_at,
                        PriceObservation.id,
                    ),
                )

    return {
        "snapshot_at": snapshot_at.isoformat(),
        "quote_window_start": quote_start.isoformat(),
        "portfolios": portfolios,
        "positions": positions,
        "transactions": transactions,
        "assets": assets,
        "observations": observations,
        "database_snapshot": "repeatable_read",
        "database_read_only": True,
        "historical_completeness_verified": False,
        "historical_availability_verified": False,
    }
