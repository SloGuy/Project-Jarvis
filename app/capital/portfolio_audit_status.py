"""Read-only structural checks for the prospective accounting audit."""

import re

from sqlalchemy import text


SOURCES = (
    "portfolios",
    "portfolio_positions",
    "portfolio_transactions",
)
INSTALLATION = "capital_accounting_audit_installation"
AUDIT = "capital_accounting_audit"
RECORD_FUNCTION = "capital_accounting_audit_record_change"
BLOCK_FUNCTION = "capital_accounting_audit_reject_destructive_change"

# PostgreSQL tgtype bitmask: ROW=1, BEFORE=2, INSERT=4,
# DELETE=8, UPDATE=16, TRUNCATE=32.
EXPECTED_TRIGGERS = {
    ("portfolios", "capital_accounting_audit_portfolios"):
        (29, RECORD_FUNCTION),
    ("portfolio_positions", "capital_accounting_audit_positions"):
        (29, RECORD_FUNCTION),
    ("portfolio_transactions", "capital_accounting_audit_transactions"):
        (29, RECORD_FUNCTION),
    **{
        (name, "capital_accounting_audit_no_truncate"):
            (34, BLOCK_FUNCTION)
        for name in SOURCES
    },
    (AUDIT, "capital_accounting_audit_preserve_records"):
        (58, BLOCK_FUNCTION),
    (INSTALLATION, "capital_accounting_audit_preserve_installation"):
        (62, BLOCK_FUNCTION),
}


def get_portfolio_audit_status(*, schema, database_engine=None):
    if (
        not isinstance(schema, str)
        or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema) is None
        or schema.startswith("pg_")
        or schema == "information_schema"
    ):
        raise ValueError("Explicit application or test schema required.")

    if database_engine is None:
        from app.market_db.database import engine
        database_engine = engine

    if database_engine.dialect.name != "postgresql":
        raise ValueError("PostgreSQL is required.")

    result = {
        "schema": schema,
        "status": "incomplete",
        "issues": [],
        "installation_id": None,
        "baseline_counts": {},
        "database_writes": False,
        "function_bodies_verified": False,
        "historical_completeness_verified": False,
        "execution_authorized": False,
    }

    with database_engine.connect() as connection:
        connection = connection.execution_options(
            isolation_level="REPEATABLE READ"
        )
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout = '30s'"))
            result["checked_at"] = connection.scalar(
                text("SELECT statement_timestamp()")
            ).isoformat()

            if connection.scalar(
                text("SELECT 1 FROM pg_namespace WHERE nspname = :schema"),
                {"schema": schema},
            ) is None:
                result["issues"].append("schema_missing")
                return result

            relations = dict(connection.execute(text("""
                SELECT c.relname, c.relkind
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema
            """), {"schema": schema}).all())

            functions = connection.execute(text("""
                SELECT p.proname
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = :schema
                  AND p.proname IN (
                    'capital_accounting_audit_record_change',
                    'capital_accounting_audit_reject_destructive_change'
                  )
            """), {"schema": schema}).scalars().all()

            if (
                AUDIT not in relations
                and INSTALLATION not in relations
                and not functions
            ):
                result["status"] = "not_installed"
                return result

            for name in (*SOURCES, INSTALLATION, AUDIT):
                if relations.get(name) != "r":
                    result["issues"].append(f"required_table_missing:{name}")
            if result["issues"]:
                return result

            quote = connection.dialect.identifier_preparer.quote_identifier
            prefix = quote(schema)
            installations = connection.execute(text(
                f"SELECT * FROM {prefix}.{INSTALLATION}"
            )).mappings().all()

            if len(installations) != 1:
                result["issues"].append("expected_one_installation")
                return result

            installation = installations[0]
            result["installation_id"] = str(installation["installation_id"])
            if (
                installation["singleton"] is not True
                or installation["schema_version"] != 1
                or installation["baseline_finished_at"] is None
            ):
                result["issues"].append("invalid_installation_metadata")
                return result

            counts = dict(connection.execute(text(
                f"SELECT source_table, count(*) FROM {prefix}.{AUDIT} "
                "WHERE operation = 'BASELINE' GROUP BY source_table"
            )).all())
            columns = (
                "baseline_portfolio_count",
                "baseline_position_count",
                "baseline_transaction_count",
            )
            for name, column in zip(SOURCES, columns):
                actual = counts.get(name, 0)
                expected = installation[column]
                result["baseline_counts"][name] = {
                    "recorded": actual,
                    "expected": expected,
                }
                if actual != expected:
                    result["issues"].append(f"baseline_count_mismatch:{name}")

            invalid = connection.scalar(text(
                f"SELECT count(*) FROM {prefix}.{AUDIT} "
                "WHERE installation_id <> CAST(:installation AS uuid) "
                "OR source_schema <> :schema "
                "OR (operation = 'BASELINE' "
                "AND database_transaction_id <> :baseline_transaction)"
            ), {
                "installation": result["installation_id"],
                "schema": schema,
                "baseline_transaction": installation["baseline_transaction_id"],
            })
            if invalid:
                result["issues"].append("event_installation_binding_mismatch")

            triggers = connection.execute(text("""
                SELECT c.relname, t.tgname, t.tgtype, t.tgenabled,
                       t.tgqual IS NULL AS unconditional,
                       t.tgnargs, t.tgattr::text AS columns,
                       p.proname, pn.nspname AS function_schema,
                       p.prosecdef
                FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_proc p ON p.oid = t.tgfoid
                JOIN pg_namespace pn ON pn.oid = p.pronamespace
                WHERE n.nspname = :schema AND NOT t.tgisinternal
            """), {"schema": schema}).mappings().all()
            found = {(row["relname"], row["tgname"]): row for row in triggers}

            for key, (kind, function) in EXPECTED_TRIGGERS.items():
                row = found.get(key)
                if row is None:
                    result["issues"].append(f"trigger_missing:{key[0]}:{key[1]}")
                elif (
                    row["tgtype"] != kind
                    or row["tgenabled"] not in ("O", "A")
                    or not row["unconditional"]
                    or row["tgnargs"] != 0
                    or row["columns"] != ""
                    or row["proname"] != function
                    or row["function_schema"] != schema
                    or row["prosecdef"]
                ):
                    result["issues"].append(f"trigger_mismatch:{key[0]}:{key[1]}")

    if not result["issues"]:
        result["status"] = "structural_checks_passed"
    return result
