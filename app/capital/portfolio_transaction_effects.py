"""Normalize recorded transactions for read-only portfolio analysis.

Transaction effects do not prove ledger completeness. Portfolio resets
delete history, so historical coverage requires a separate check.
"""

from decimal import Decimal, InvalidOperation, localcontext

from app.capital.portfolio_daily_returns import utc_timestamp


def _decimal(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number.")

    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError(
            f"{name} must be a finite number."
        ) from error

    if not result.is_finite():
        raise ValueError(f"{name} must be a finite number.")

    return result


def _positive_id(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a positive integer.")
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def transaction_effect(record):
    """Return signed cash and quantity changes from one ledger row."""
    transaction_id = _positive_id(record["id"], "id")
    created_at = utc_timestamp(record["created_at"])
    kind = record["transaction_type"]

    if kind not in ("buy", "sell", "deposit", "withdrawal"):
        raise ValueError(f"Unsupported transaction type: {kind!r}")

    quantity = _decimal(record["quantity"], "quantity")
    price = _decimal(record["price_usd"], "price_usd")
    total = _decimal(record["total_usd"], "total_usd")
    fees = _decimal(record["fees_usd"], "fees_usd")
    asset_id = record["asset_id"]

    if quantity <= 0 or price <= 0:
        raise ValueError("Quantity and price must be positive.")
    if total < 0 or fees < 0:
        raise ValueError("Total and fees must not be negative.")

    external_flow = kind in ("deposit", "withdrawal")

    with localcontext() as context:
        context.prec = 60

        if external_flow:
            if asset_id is not None:
                raise ValueError("Cash transactions must not have an asset.")
            if total <= 0 or quantity != total or price != Decimal("1"):
                raise ValueError("Invalid cash transaction amounts.")
            if fees != 0:
                raise ValueError("Cash transaction fees are unsupported.")

            cash_delta = total if kind == "deposit" else -total
            quantity_delta = Decimal("0")
        else:
            asset_id = _positive_id(asset_id, "asset_id")

            # Use the stored rounded total, not quantity * price.
            if kind == "buy":
                cash_delta = -(total + fees)
                quantity_delta = quantity
            else:
                if fees > total:
                    raise ValueError("Sale fees exceed sale proceeds.")
                cash_delta = total - fees
                quantity_delta = -quantity

    return {
        "transaction_id": transaction_id,
        "created_at": created_at.isoformat(),
        "transaction_type": kind,
        "asset_id": asset_id,
        "cash_delta_usd": str(cash_delta),
        "quantity_delta": str(quantity_delta),
        "external_cash_flow": external_flow,
    }


def normalize_transactions(records):
    """Validate, reject duplicate IDs, and sort without mutating input."""
    effects = [transaction_effect(record) for record in records]
    identifiers = [row["transaction_id"] for row in effects]

    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate transaction IDs.")

    return sorted(
        effects,
        key=lambda row: (
            utc_timestamp(row["created_at"]),
            row["transaction_id"],
        ),
    )
