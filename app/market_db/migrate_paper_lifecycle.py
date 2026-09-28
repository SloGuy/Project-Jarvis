"""Install paper lifecycle storage in an explicit PostgreSQL schema."""

import argparse
import re

from sqlalchemy import inspect, text

from app.market_db.database import engine
from app.capital.paper_lifecycle_store import PaperLifecycleRecord


def migrate_paper_lifecycle(*, schema, database_engine=None):
    if (
        not isinstance(schema, str)
        or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema) is None
    ):
        raise ValueError("Supply a valid lowercase PostgreSQL schema name.")

    selected_engine = (
        database_engine if database_engine is not None else engine
    )
    if selected_engine.dialect.name != "postgresql":
        raise ValueError("This migration requires PostgreSQL.")

    with selected_engine.connect() as connection:
        connection = connection.execution_options(
            schema_translate_map={None: schema}
        )
        with connection.begin():
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SET LOCAL statement_timeout = '60s'"))

            # Serialize this migration within the selected database/schema.
            connection.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:identity, 0))"
                ),
                {"identity": f"jarvis:paper-lifecycle:{schema}"},
            )

            inspector = inspect(connection)
            if not inspector.has_schema(schema):
                raise ValueError("Target schema does not exist.")

            existing = set(inspector.get_table_names(schema=schema))
            required = {"portfolios", "capital_experiment_factory"}
            missing = required - existing
            if missing:
                raise ValueError(
                    "Missing prerequisite tables: "
                    + ", ".join(sorted(missing))
                )

            table = PaperLifecycleRecord.__table__
            if table.name in existing:
                raise ValueError(
                    "Lifecycle table already exists; installation refused."
                )

            # Create only this table, including its constraints and indexes.
            # PostgreSQL rolls back the DDL if installation fails.
            table.create(connection, checkfirst=False)

            installed = inspect(connection)
            foreign_keys = installed.get_foreign_keys(
                table.name, schema=schema
            )
            actual = {
                (
                    tuple(item["constrained_columns"]),
                    item["referred_table"],
                    tuple(item["referred_columns"]),
                )
                for item in foreign_keys
            }
            expected = {
                (
                    ("request_key",),
                    "capital_experiment_factory",
                    ("request_key",),
                ),
                (("portfolio_id",), "portfolios", ("id",)),
            }
            if actual != expected:
                raise RuntimeError("Installed foreign keys differ.")

    return {
        "schema": schema,
        "table": PaperLifecycleRecord.__tablename__,
        "status": "installed",
        "portfolios_modified": False,
        "experiments_activated": False,
    }


def main():
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", required=True)
    args = parser.parse_args()
    result = migrate_paper_lifecycle(schema=args.schema)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
