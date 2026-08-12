from sqlalchemy import text

from app.market_db.database import engine


MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS autonomous_trade_decisions (
        id SERIAL PRIMARY KEY,

        portfolio_id INTEGER NOT NULL
            REFERENCES portfolios(id)
            ON DELETE CASCADE,

        asset_id INTEGER NOT NULL
            REFERENCES market_assets(id)
            ON DELETE CASCADE,

        policy_name VARCHAR(120) NOT NULL,
        strategy_name VARCHAR(120) NOT NULL,

        action VARCHAR(20) NOT NULL,

        quantity NUMERIC(28, 12) NOT NULL,
        reference_price_usd NUMERIC(20, 8) NOT NULL,

        price_observed_at TIMESTAMPTZ NOT NULL,

        confidence_percent NUMERIC(8, 4) NOT NULL,
        rationale TEXT NOT NULL,

        approved BOOLEAN NOT NULL,
        rejection_reasons JSON NOT NULL,

        execution_status VARCHAR(30)
            NOT NULL
            DEFAULT 'not_executed',

        execution_attempted_at TIMESTAMPTZ,
        executed_at TIMESTAMPTZ,

        portfolio_transaction_id INTEGER
            REFERENCES portfolio_transactions(id)
            ON DELETE SET NULL,

        execution_error TEXT,

        created_at TIMESTAMPTZ NOT NULL,
        evaluated_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    ALTER TABLE autonomous_trade_decisions
    ADD COLUMN IF NOT EXISTS execution_status VARCHAR(30)
        NOT NULL
        DEFAULT 'not_executed'
    """,
    """
    ALTER TABLE autonomous_trade_decisions
    ADD COLUMN IF NOT EXISTS execution_attempted_at TIMESTAMPTZ
    """,
    """
    ALTER TABLE autonomous_trade_decisions
    ADD COLUMN IF NOT EXISTS executed_at TIMESTAMPTZ
    """,
    """
    ALTER TABLE autonomous_trade_decisions
    ADD COLUMN IF NOT EXISTS portfolio_transaction_id INTEGER
        REFERENCES portfolio_transactions(id)
        ON DELETE SET NULL
    """,
    """
    ALTER TABLE autonomous_trade_decisions
    ADD COLUMN IF NOT EXISTS execution_error TEXT
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_autonomous_trade_decisions_portfolio_created
    ON autonomous_trade_decisions (
        portfolio_id,
        created_at
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_autonomous_trade_decisions_asset_created
    ON autonomous_trade_decisions (
        asset_id,
        created_at
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_autonomous_trade_decisions_approved_created
    ON autonomous_trade_decisions (
        approved,
        created_at
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_autonomous_trade_decisions_execution_status
    ON autonomous_trade_decisions (
        execution_status,
        created_at
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
    uq_autonomous_trade_decisions_transaction
    ON autonomous_trade_decisions (
        portfolio_transaction_id
    )
    WHERE portfolio_transaction_id IS NOT NULL
    """,
)


def migrate_autonomous_trading() -> None:
    with engine.begin() as connection:
        for statement in MIGRATION_STATEMENTS:
            connection.execute(text(statement))

    print(
        "Autonomous-trading database migration "
        "completed successfully."
    )


if __name__ == "__main__":
    migrate_autonomous_trading()
