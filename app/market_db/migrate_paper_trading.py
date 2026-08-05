from sqlalchemy import text

from app.market_db.database import engine


MIGRATION_STATEMENTS = (
    """
    ALTER TABLE portfolio_transactions
    ALTER COLUMN asset_id DROP NOT NULL
    """,
    """
    ALTER TABLE portfolio_transactions
    ADD COLUMN IF NOT EXISTS realized_gain_loss_usd NUMERIC(20, 8)
    """,
)


def migrate_paper_trading() -> None:
    with engine.begin() as connection:
        for statement in MIGRATION_STATEMENTS:
            connection.execute(text(statement))

    print("Paper-trading database migration completed successfully.")


if __name__ == "__main__":
    migrate_paper_trading()
