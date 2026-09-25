-- Prospective accounting audit, schema version 1.
-- Installed only by the controlled migration.
--
-- No foreign keys to accounting tables: audit evidence must survive
-- source-row deletion.
--
-- Event IDs and transaction IDs are identifiers, not commit ordering.
-- recorded_at is trigger execution time, not commit time.
--
-- Row images are JSON text so clients can parse financial numbers using
-- Decimal without silently converting them to binary floating point.

CREATE TABLE capital_accounting_audit_installation (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    installation_id UUID NOT NULL UNIQUE,
    baseline_started_at TIMESTAMPTZ NOT NULL,
    baseline_finished_at TIMESTAMPTZ,
    baseline_transaction_id BIGINT NOT NULL,
    baseline_portfolio_count BIGINT NOT NULL DEFAULT 0
        CHECK (baseline_portfolio_count >= 0),
    baseline_position_count BIGINT NOT NULL DEFAULT 0
        CHECK (baseline_position_count >= 0),
    baseline_transaction_count BIGINT NOT NULL DEFAULT 0
        CHECK (baseline_transaction_count >= 0),
    CHECK (
        baseline_finished_at IS NULL
        OR baseline_finished_at >= baseline_started_at
    )
);

CREATE TABLE capital_accounting_audit (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    installation_id UUID NOT NULL REFERENCES
        capital_accounting_audit_installation (installation_id),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    database_transaction_id BIGINT NOT NULL DEFAULT txid_current(),
    database_session_user TEXT NOT NULL DEFAULT session_user,
    source_schema TEXT NOT NULL,
    source_table TEXT NOT NULL CHECK (
        source_table IN (
            'portfolios',
            'portfolio_positions',
            'portfolio_transactions'
        )
    ),
    operation TEXT NOT NULL CHECK (
        operation IN ('BASELINE', 'INSERT', 'UPDATE', 'DELETE')
    ),
    source_row_id BIGINT NOT NULL,
    old_portfolio_id BIGINT,
    new_portfolio_id BIGINT,
    old_row_json TEXT,
    new_row_json TEXT,

    CHECK (
        (
            operation IN ('BASELINE', 'INSERT')
            AND old_row_json IS NULL
            AND new_row_json IS NOT NULL
            AND old_portfolio_id IS NULL
            AND new_portfolio_id IS NOT NULL
        )
        OR (
            operation = 'UPDATE'
            AND old_row_json IS NOT NULL
            AND new_row_json IS NOT NULL
            AND old_portfolio_id IS NOT NULL
            AND new_portfolio_id IS NOT NULL
        )
        OR (
            operation = 'DELETE'
            AND old_row_json IS NOT NULL
            AND new_row_json IS NULL
            AND old_portfolio_id IS NOT NULL
            AND new_portfolio_id IS NULL
        )
    ),

    CHECK (
        old_row_json IS NULL
        OR json_typeof(old_row_json::json) = 'object'
    ),
    CHECK (
        new_row_json IS NULL
        OR json_typeof(new_row_json::json) = 'object'
    )
);

CREATE INDEX ix_capital_accounting_audit_transaction
    ON capital_accounting_audit (database_transaction_id, event_id);

CREATE INDEX ix_capital_accounting_audit_old_portfolio
    ON capital_accounting_audit (old_portfolio_id, event_id);

CREATE INDEX ix_capital_accounting_audit_new_portfolio
    ON capital_accounting_audit (new_portfolio_id, event_id);

CREATE INDEX ix_capital_accounting_audit_source_row
    ON capital_accounting_audit (source_table, source_row_id, event_id);
