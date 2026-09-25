"""Atomically install prospective portfolio accounting audit.

Do not run against production until isolated PostgreSQL tests pass.
Existing audit installations are never replaced or silently upgraded.
"""

import argparse
from pathlib import Path
import re
from uuid import uuid4

from sqlalchemy import text


SOURCE_TABLES = (
    "portfolios",
    "portfolio_positions",
    "portfolio_transactions",
)

AUDIT_TABLES = (
    "capital_accounting_audit_installation",
    "capital_accounting_audit",
)

SQL_DIRECTORY = Path(__file__).resolve().parent


def _schema_name(value):
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value) is None
        or value.startswith("pg_")
        or value == "information_schema"
    ):
        raise ValueError("An explicit application or test schema is required.")
    return value


def _execute_sql_file(connection, filename):
    """Execute fixed repository SQL without parameter interpolation."""
    source = (SQL_DIRECTORY / filename).read_text(encoding="utf-8")
    cursor = connection.connection.driver_connection.cursor()
    try:
        # psycopg simple-query execution supports these multi-statement files.
        cursor.execute(source, prepare=False)
    finally:
        cursor.close()


def migrate_portfolio_audit(*, schema, database_engine=None):
    schema = _schema_name(schema)

    if database_engine is None:
        from app.market_db.database import engine

        database_engine = engine

    if (
        database_engine.dialect.name != "postgresql"
        or database_engine.dialect.driver != "psycopg"
    ):
        raise ValueError("Migration requires PostgreSQL with psycopg.")

    installation_id = str(uuid4())

    with database_engine.connect() as connection:
        # Baseline queries must see writes committed before locks are acquired.
        connection = connection.execution_options(
            isolation_level="READ COMMITTED"
        )
        with connection.begin():
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SET LOCAL statement_timeout = '60s'"))
            connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))

            exists = connection.scalar(
                text("SELECT 1 FROM pg_namespace WHERE nspname = :schema"),
                {"schema": schema},
            )
            if exists is None:
                raise ValueError("Selected schema does not exist.")

            quote = connection.dialect.identifier_preparer.quote_identifier
            qualified_schema = quote(schema)

            # Source tables must exist in this schema, not via search-path fallback.
            for name in SOURCE_TABLES:
                kind = connection.scalar(
                    text(
                        "SELECT c.relkind FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :schema AND c.relname = :name"
                    ),
                    {"schema": schema, "name": name},
                )
                if kind != "r":
                    raise ValueError(
                        f"Required ordinary table is missing or unsupported: {name}"
                    )

            source_names = ", ".join(
                f"{qualified_schema}.{quote(name)}"
                for name in SOURCE_TABLES
            )
            connection.execute(text(
                f"LOCK TABLE {source_names} IN ACCESS EXCLUSIVE MODE"
            ))

            for name in AUDIT_TABLES:
                exists = connection.scalar(
                    text(
                        "SELECT 1 FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :schema AND c.relname = :name"
                    ),
                    {"schema": schema, "name": name},
                )
                if exists is not None:
                    raise RuntimeError(
                        "Audit objects already exist; inspect before retrying."
                    )

            connection.execute(text(
                f"SET LOCAL search_path = {qualified_schema}, pg_catalog"
            ))
            _execute_sql_file(connection, "portfolio_audit_schema.sql")

            installation_table = (
                f"{qualified_schema}.capital_accounting_audit_installation"
            )
            audit_table = f"{qualified_schema}.capital_accounting_audit"

            connection.execute(
                text(
                    f"INSERT INTO {installation_table} ("
                    "singleton, schema_version, installation_id, "
                    "baseline_started_at, baseline_transaction_id"
                    ") VALUES (TRUE, 1, CAST(:installation AS uuid), "
                    "clock_timestamp(), txid_current())"
                ),
                {"installation": installation_id},
            )

            counts = {}
            for name in SOURCE_TABLES:
                portfolio_column = (
                    "source.id" if name == "portfolios"
                    else "source.portfolio_id"
                )
                result = connection.execute(
                    text(
                        f"INSERT INTO {audit_table} ("
                        "installation_id, source_schema, source_table, "
                        "operation, source_row_id, new_portfolio_id, new_row_json"
                        ") SELECT CAST(:installation AS uuid), :schema, :table, "
                        f"'BASELINE', source.id, {portfolio_column}, "
                        "row_to_json(source)::text "
                        f"FROM {qualified_schema}.{quote(name)} AS source "
                        "ORDER BY source.id"
                    ),
                    {
                        "installation": installation_id,
                        "schema": schema,
                        "table": name,
                    },
                )
                counts[name] = result.rowcount

            connection.execute(
                text(
                    f"UPDATE {installation_table} SET "
                    "baseline_finished_at = clock_timestamp(), "
                    "baseline_portfolio_count = :portfolios, "
                    "baseline_position_count = :positions, "
                    "baseline_transaction_count = :transactions "
                    "WHERE singleton = TRUE"
                ),
                {
                    "portfolios": counts["portfolios"],
                    "positions": counts["portfolio_positions"],
                    "transactions": counts["portfolio_transactions"],
                },
            )

            # Installed after baseline finalization, while source locks remain held.
            _execute_sql_file(connection, "portfolio_audit_triggers.sql")

    return {
        "schema": schema,
        "installation_id": installation_id,
        "baseline_counts": counts,
        "status": "installed",
        "historical_coverage_before_installation_verified": False,
    }


def main():
    import json

    parser = argparse.ArgumentParser(
        description="Install accounting audit after isolated migration tests."
    )
    parser.add_argument("--schema", required=True)
    args = parser.parse_args()
    result = migrate_portfolio_audit(schema=args.schema)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
