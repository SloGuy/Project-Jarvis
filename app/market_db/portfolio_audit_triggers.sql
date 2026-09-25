-- Installed by the controlled migration in its selected schema.
-- Functions use invoker privileges and schema-qualified dynamic SQL.
--
-- These safeguards do not protect against a database owner or
-- administrator disabling triggers or altering database objects.

CREATE FUNCTION capital_accounting_audit_record_change()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    installation uuid;
    old_image json;
    new_image json;
    old_portfolio bigint;
    new_portfolio bigint;
    row_identifier bigint;
BEGIN
    IF TG_TABLE_NAME NOT IN (
        'portfolios',
        'portfolio_positions',
        'portfolio_transactions'
    ) THEN
        RAISE EXCEPTION 'Unsupported accounting audit source';
    END IF;

    IF TG_OP NOT IN ('INSERT', 'UPDATE', 'DELETE') THEN
        RAISE EXCEPTION 'Unsupported accounting audit operation';
    END IF;

    EXECUTE format(
        'SELECT installation_id FROM %I.capital_accounting_audit_installation
         WHERE singleton = TRUE',
        TG_TABLE_SCHEMA
    )
    INTO STRICT installation;

    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        old_image := row_to_json(OLD);
        old_portfolio := CASE
            WHEN TG_TABLE_NAME = 'portfolios'
                THEN (old_image ->> 'id')::bigint
            ELSE (old_image ->> 'portfolio_id')::bigint
        END;
    END IF;

    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        new_image := row_to_json(NEW);
        new_portfolio := CASE
            WHEN TG_TABLE_NAME = 'portfolios'
                THEN (new_image ->> 'id')::bigint
            ELSE (new_image ->> 'portfolio_id')::bigint
        END;
        row_identifier := (new_image ->> 'id')::bigint;
    ELSE
        row_identifier := (old_image ->> 'id')::bigint;
    END IF;

    EXECUTE format(
        'INSERT INTO %I.capital_accounting_audit (
            installation_id,
            source_schema,
            source_table,
            operation,
            source_row_id,
            old_portfolio_id,
            new_portfolio_id,
            old_row_json,
            new_row_json
         ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)',
        TG_TABLE_SCHEMA
    )
    USING
        installation,
        TG_TABLE_SCHEMA,
        TG_TABLE_NAME,
        TG_OP,
        row_identifier,
        old_portfolio,
        new_portfolio,
        old_image::text,
        new_image::text;

    -- Return value is ignored for AFTER triggers.
    RETURN NULL;
END;
$$;

CREATE FUNCTION capital_accounting_audit_reject_destructive_change()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION
        'Operation % on %.% is blocked by accounting audit safeguards',
        TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME;
    RETURN NULL;
END;
$$;

CREATE TRIGGER capital_accounting_audit_portfolios
AFTER INSERT OR UPDATE OR DELETE ON portfolios
FOR EACH ROW
EXECUTE FUNCTION capital_accounting_audit_record_change();

CREATE TRIGGER capital_accounting_audit_positions
AFTER INSERT OR UPDATE OR DELETE ON portfolio_positions
FOR EACH ROW
EXECUTE FUNCTION capital_accounting_audit_record_change();

CREATE TRIGGER capital_accounting_audit_transactions
AFTER INSERT OR UPDATE OR DELETE ON portfolio_transactions
FOR EACH ROW
EXECUTE FUNCTION capital_accounting_audit_record_change();

CREATE TRIGGER capital_accounting_audit_no_truncate
BEFORE TRUNCATE ON portfolios
FOR EACH STATEMENT
EXECUTE FUNCTION capital_accounting_audit_reject_destructive_change();

CREATE TRIGGER capital_accounting_audit_no_truncate
BEFORE TRUNCATE ON portfolio_positions
FOR EACH STATEMENT
EXECUTE FUNCTION capital_accounting_audit_reject_destructive_change();

CREATE TRIGGER capital_accounting_audit_no_truncate
BEFORE TRUNCATE ON portfolio_transactions
FOR EACH STATEMENT
EXECUTE FUNCTION capital_accounting_audit_reject_destructive_change();

CREATE TRIGGER capital_accounting_audit_preserve_records
BEFORE UPDATE OR DELETE OR TRUNCATE ON capital_accounting_audit
FOR EACH STATEMENT
EXECUTE FUNCTION capital_accounting_audit_reject_destructive_change();

-- Install this file after the migration has finalized baseline metadata.
CREATE TRIGGER capital_accounting_audit_preserve_installation
BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
ON capital_accounting_audit_installation
FOR EACH STATEMENT
EXECUTE FUNCTION capital_accounting_audit_reject_destructive_change();
