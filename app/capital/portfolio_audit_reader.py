"""Read accounting audit evidence from one consistent PostgreSQL snapshot.

Event ordering is for presentation only, not transaction commit order.
Current source row images remain JSON text to preserve numeric precision.
"""
from sqlalchemy import text

from app.capital.portfolio_audit_events import decode_audit_event
from app.capital.portfolio_daily_returns import utc_timestamp
from app.market_db.migrate_portfolio_audit import (
    SOURCE_TABLES,
    _schema_name,
)


def _bounded_rows(connection, statement, maximum):
    rows = connection.execute(
        text(statement),
        {"limit": maximum + 1},
    ).mappings().all()
    if len(rows) > maximum:
        raise ValueError("Snapshot row limit exceeded; evidence was not truncated.")
    return [dict(row) for row in rows]


def read_portfolio_audit(
    *, schema, database_engine=None, maximum_rows=100000,
    include_assets=False,
):
    """Read all visible events and source rows without an incremental cursor.

    The row limit applies separately to events and each source table.
    This function validates event contracts, not continuity or trigger bodies.
    """
    if type(include_assets) is not bool:
        raise ValueError("include_assets must be a boolean.")
    schema = _schema_name(schema)
    if type(maximum_rows) is not int or not 1 <= maximum_rows <= 1000000:
        raise ValueError("maximum_rows must be an integer from 1 to 1000000.")

    if database_engine is None:
        from app.market_db.database import engine

        database_engine = engine

    if database_engine.dialect.name != "postgresql":
        raise ValueError("Audit reader requires PostgreSQL.")

    with database_engine.connect() as connection:
        connection = connection.execution_options(
            isolation_level="REPEATABLE READ"
        )
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout = '30s'"))
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))

            snapshot = connection.execute(text(
                "SELECT statement_timestamp() AS sampled_at, "
                "pg_current_snapshot()::text AS visibility_snapshot"
            )).mappings().one()
            sampled_at = utc_timestamp(snapshot["sampled_at"]).isoformat()

            quote = connection.dialect.identifier_preparer.quote_identifier
            prefix = quote(schema)
            metadata_rows = connection.execute(text(
                f"SELECT * FROM {prefix}.capital_accounting_audit_installation"
            )).mappings().all()
            if len(metadata_rows) != 1:
                raise ValueError("Expected exactly one audit installation.")

            installation = dict(metadata_rows[0])
            if (
                installation["singleton"] is not True
                or installation["schema_version"] != 1
                or installation["baseline_finished_at"] is None
            ):
                raise ValueError("Audit installation is incomplete or unsupported.")

            started = utc_timestamp(installation["baseline_started_at"])
            finished = utc_timestamp(installation["baseline_finished_at"])
            if finished < started:
                raise ValueError("Invalid baseline timestamps.")

            installation_id = str(installation["installation_id"])
            raw_events = _bounded_rows(
                connection,
                f"SELECT * FROM {prefix}.capital_accounting_audit "
                "ORDER BY event_id LIMIT :limit",
                maximum_rows,
            )
            events = [
                decode_audit_event(
                    event=row,
                    schema=schema,
                    installation_id=installation_id,
                )
                for row in raw_events
            ]

            counts = {name: 0 for name in SOURCE_TABLES}
            for event in events:
                if event["operation"] == "BASELINE":
                    if (
                        event["database_transaction_id"]
                        != installation["baseline_transaction_id"]
                    ):
                        raise ValueError("Baseline transaction binding differs.")
                    counts[event["source_table"]] += 1

            expected = dict(zip(SOURCE_TABLES, (
                installation["baseline_portfolio_count"],
                installation["baseline_position_count"],
                installation["baseline_transaction_count"],
            )))
            if counts != expected:
                raise ValueError("Baseline event counts differ from metadata.")

            current_rows = {}
            for name in SOURCE_TABLES:
                current_rows[name] = _bounded_rows(
                    connection,
                    f"SELECT source.id AS source_row_id, "
                    "row_to_json(source)::text AS row_json "
                    f"FROM {prefix}.{quote(name)} AS source "
                    "ORDER BY source.id LIMIT :limit",
                    maximum_rows,
                )

            asset_rows = []
            if include_assets:
                asset_rows = _bounded_rows(
                    connection,
                    "SELECT asset.id AS source_row_id, "
                    "row_to_json(asset)::text AS row_json "
                    f"FROM {prefix}.market_assets AS asset "
                    f"WHERE asset.id IN (SELECT asset_id FROM "
                    f"{prefix}.portfolio_positions WHERE quantity > 0) "
                    "ORDER BY asset.id LIMIT :limit",
                    maximum_rows,
                )

    installation["installation_id"] = installation_id
    installation["baseline_started_at"] = started.isoformat()
    installation["baseline_finished_at"] = finished.isoformat()

    return {
        "schema": schema,
        "sampled_at": sampled_at,
        "visibility_snapshot": snapshot["visibility_snapshot"],
        "installation": installation,
        "events": events,
        "current_rows": current_rows,
        "asset_rows": asset_rows,
        "asset_metadata_requested": include_assets,
        "event_count": len(events),
        "database_snapshot": "repeatable_read",
        "database_read_only": True,
        "database_writes": False,
        "continuity_verified": False,
        "commit_order_verified": False,
        "function_bodies_verified": False,
        "historical_completeness_verified": False,
        "execution_authorized": False,
    }
