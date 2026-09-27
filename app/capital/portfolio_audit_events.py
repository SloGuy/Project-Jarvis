"""Validate accounting audit row images without interpreting commit order."""

import json
from decimal import Decimal
from uuid import UUID

from app.capital.portfolio_daily_returns import utc_timestamp


SOURCES = {
    "portfolios",
    "portfolio_positions",
    "portfolio_transactions",
}


def _positive_id(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Expected a positive integer identifier.")
    return value


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field.")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Nonfinite JSON number.")


def _image(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Row image must be JSON text.")
    result = json.loads(
        value,
        parse_float=Decimal,
        parse_constant=_invalid_constant,
        object_pairs_hook=_unique_fields,
    )
    if not isinstance(result, dict):
        raise ValueError("Row image must contain an object.")
    _positive_id(result.get("id"))
    return result


def decode_audit_event(*, event, schema, installation_id):
    """Validate an event's binding, operation, and row identities.

    Financial business rules and continuity require additional checks.
    Decoded Decimal values must not be serialized through binary floats.
    """
    if not isinstance(schema, str) or not schema:
        raise ValueError("Expected source schema is required.")

    expected_installation = UUID(str(installation_id))
    if UUID(str(event["installation_id"])) != expected_installation:
        raise ValueError("Audit installation mismatch.")
    if event["source_schema"] != schema:
        raise ValueError("Audit source schema mismatch.")

    source = event["source_table"]
    operation = event["operation"]
    if source not in SOURCES:
        raise ValueError("Unsupported audit source.")
    if operation not in ("BASELINE", "INSERT", "UPDATE", "DELETE"):
        raise ValueError("Unsupported audit operation.")

    event_id = _positive_id(event["event_id"])
    transaction_id = _positive_id(event["database_transaction_id"])
    source_id = _positive_id(event["source_row_id"])
    recorded_at = utc_timestamp(event["recorded_at"])
    before = _image(event["old_row_json"])
    after = _image(event["new_row_json"])

    required_before = operation in ("UPDATE", "DELETE")
    required_after = operation in ("BASELINE", "INSERT", "UPDATE")
    if (before is not None) != required_before:
        raise ValueError("Unexpected old row image.")
    if (after is not None) != required_after:
        raise ValueError("Unexpected new row image.")

    for image, portfolio_id in (
        (before, event["old_portfolio_id"]),
        (after, event["new_portfolio_id"]),
    ):
        if image is None:
            if portfolio_id is not None:
                raise ValueError("Portfolio identity has no corresponding image.")
            continue

        expected_portfolio = _positive_id(
            image["id"] if source == "portfolios"
            else image.get("portfolio_id")
        )
        if _positive_id(portfolio_id) != expected_portfolio:
            raise ValueError("Row image portfolio identity mismatch.")

    # UPDATE records use the new row ID; an ID change remains visible
    # in the old image and must be handled explicitly during reconstruction.
    identifying_image = after if after is not None else before
    if identifying_image["id"] != source_id:
        raise ValueError("Source row identity mismatch.")

    return {
        "event_id": event_id,
        "installation_id": str(expected_installation),
        "database_transaction_id": transaction_id,
        "recorded_at": recorded_at.isoformat(),
        "source_schema": schema,
        "source_table": source,
        "operation": operation,
        "source_row_id": source_id,
        "old_portfolio_id": event["old_portfolio_id"],
        "new_portfolio_id": event["new_portfolio_id"],
        "before": before,
        "after": after,
    }
