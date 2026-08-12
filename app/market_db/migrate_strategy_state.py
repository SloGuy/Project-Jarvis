from sqlalchemy import text

from app.market_db.database import engine


MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS autonomous_strategy_state (
        id SERIAL PRIMARY KEY,

        asset_id INTEGER NOT NULL
            REFERENCES market_assets(id)
            ON DELETE CASCADE,

        strategy_name VARCHAR(120) NOT NULL,

        pending_action VARCHAR(20),

        confirmation_count INTEGER NOT NULL DEFAULT 0,

        first_confirmed_at TIMESTAMPTZ,
        last_confirmed_at TIMESTAMPTZ,

        last_observation_at TIMESTAMPTZ,

        updated_at TIMESTAMPTZ NOT NULL,

        CONSTRAINT uq_autonomous_strategy_state_asset_strategy
            UNIQUE (asset_id, strategy_name)
    )
    """,
    """
    ALTER TABLE autonomous_strategy_state
    ADD COLUMN IF NOT EXISTS last_observation_at TIMESTAMPTZ
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_autonomous_strategy_state_strategy_action
    ON autonomous_strategy_state (
        strategy_name,
        pending_action
    )
    """,
)


def migrate_strategy_state() -> None:
    with engine.begin() as connection:
        for statement in MIGRATION_STATEMENTS:
            connection.execute(text(statement))

    print(
        "Autonomous strategy-state migration "
        "completed successfully."
    )


if __name__ == "__main__":
    migrate_strategy_state()
