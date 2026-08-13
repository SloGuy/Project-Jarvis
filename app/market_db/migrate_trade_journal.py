from sqlalchemy import text

from app.market_db.database import engine


MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS autonomous_trade_journal (
        id SERIAL PRIMARY KEY,

        portfolio_id INTEGER NOT NULL
            REFERENCES portfolios(id)
            ON DELETE CASCADE,

        asset_id INTEGER NOT NULL
            REFERENCES market_assets(id)
            ON DELETE CASCADE,

        status VARCHAR(20)
            NOT NULL
            DEFAULT 'open',

        strategy_name VARCHAR(120) NOT NULL,

        entry_decision_id INTEGER NOT NULL
            REFERENCES autonomous_trade_decisions(id)
            ON DELETE CASCADE,

        entry_transaction_id INTEGER NOT NULL
            REFERENCES portfolio_transactions(id)
            ON DELETE CASCADE,

        entry_quantity NUMERIC(28, 12) NOT NULL,
        entry_price_usd NUMERIC(20, 8) NOT NULL,
        entry_confidence_percent NUMERIC(8, 4) NOT NULL,

        entry_rationale TEXT NOT NULL,
        entry_market_context JSON NOT NULL,

        expected_outcome TEXT,

        opened_at TIMESTAMPTZ NOT NULL,

        exit_decision_id INTEGER
            REFERENCES autonomous_trade_decisions(id)
            ON DELETE SET NULL,

        exit_transaction_id INTEGER
            REFERENCES portfolio_transactions(id)
            ON DELETE SET NULL,

        exit_price_usd NUMERIC(20, 8),

        exit_rationale TEXT,
        exit_rule VARCHAR(60),

        exit_market_context JSON,

        closed_at TIMESTAMPTZ,

        holding_duration_seconds INTEGER,

        realized_gain_loss_usd NUMERIC(20, 8),
        return_percent NUMERIC(12, 6),

        actual_outcome TEXT,
        thesis_correct BOOLEAN,

        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_autonomous_trade_journal_portfolio_status
    ON autonomous_trade_journal (
        portfolio_id,
        status
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_autonomous_trade_journal_asset_status
    ON autonomous_trade_journal (
        asset_id,
        status
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_autonomous_trade_journal_opened_at
    ON autonomous_trade_journal (
        opened_at
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_autonomous_trade_journal_closed_at
    ON autonomous_trade_journal (
        closed_at
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
    uq_autonomous_trade_journal_entry_decision
    ON autonomous_trade_journal (
        entry_decision_id
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
    uq_autonomous_trade_journal_entry_transaction
    ON autonomous_trade_journal (
        entry_transaction_id
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
    uq_autonomous_trade_journal_exit_transaction
    ON autonomous_trade_journal (
        exit_transaction_id
    )
    WHERE exit_transaction_id IS NOT NULL
    """,
)


def migrate_trade_journal() -> None:
    with engine.begin() as connection:
        for statement in MIGRATION_STATEMENTS:
            connection.execute(text(statement))

    print(
        "Autonomous trade-journal migration "
        "completed successfully."
    )


if __name__ == "__main__":
    migrate_trade_journal()
