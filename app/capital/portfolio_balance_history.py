"""Read-only balance reconstruction from a consistent account snapshot.

The caller must read cash, positions, and transactions from the same
database snapshot. Supply all surviving transactions through snapshot_at.

Reconstruction does not prove ledger completeness or price quality.
It must not, by itself, make an equity valuation usable.
"""

from decimal import Decimal, InvalidOperation, localcontext

from app.capital.portfolio_daily_returns import utc_timestamp
from app.capital.portfolio_transaction_effects import normalize_transactions


def _nonnegative(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a nonnegative finite number.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError(
            f"{name} must be a nonnegative finite number."
        ) from error
    if not result.is_finite() or result < 0:
        raise ValueError(f"{name} must be a nonnegative finite number.")
    return result


def reconstruct_balances(
    *,
    snapshot_at,
    cash_balance_usd,
    positions,
    transactions,
    measurement_times,
):
    """Reconstruct balances, excluding unsupported historical dates.

    positions maps integer asset IDs to current quantities.
    Transactions at a measurement timestamp are included in that balance.
    """
    cutoff = utc_timestamp(snapshot_at)
    cash = _nonnegative(cash_balance_usd, "cash_balance_usd")

    if not isinstance(positions, dict):
        raise ValueError("positions must map asset IDs to quantities.")

    quantities = {}
    for asset_id, quantity in positions.items():
        if (
            isinstance(asset_id, bool)
            or not isinstance(asset_id, int)
            or asset_id <= 0
        ):
            raise ValueError("Asset IDs must be positive integers.")
        quantities[asset_id] = _nonnegative(quantity, "quantity")

    effects = normalize_transactions(transactions)
    timed_effects = [
        (utc_timestamp(row["created_at"]), row)
        for row in effects
    ]
    if any(at > cutoff for at, _ in timed_effects):
        raise ValueError("Transaction occurs after the account snapshot.")

    marks = sorted(utc_timestamp(value) for value in measurement_times)
    if len(marks) != len(set(marks)):
        raise ValueError("Duplicate measurement timestamps.")
    if any(at > cutoff for at in marks):
        raise ValueError("Measurement occurs after the account snapshot.")

    earliest = timed_effects[0][0] if timed_effects else None
    accepted = []
    excluded = []

    for at in marks:
        if at == cutoff:
            accepted.append(at)
        elif earliest is None:
            excluded.append({
                "measured_at": at.isoformat(),
                "reason": "no_surviving_transaction_history",
            })
        elif at < earliest:
            excluded.append({
                "measured_at": at.isoformat(),
                "reason": "before_surviving_transaction_history",
            })
        else:
            accepted.append(at)

    points = []
    index = len(timed_effects) - 1

    with localcontext() as context:
        context.prec = 60

        for at in reversed(accepted):
            while index >= 0 and timed_effects[index][0] > at:
                _, effect = timed_effects[index]
                cash -= Decimal(effect["cash_delta_usd"])

                asset_id = effect["asset_id"]
                if asset_id is not None:
                    quantities[asset_id] = (
                        quantities.get(asset_id, Decimal("0"))
                        - Decimal(effect["quantity_delta"])
                    )
                    if quantities[asset_id] < 0:
                        raise ValueError(
                            "Ledger reversal produced a negative position."
                        )

                if cash < 0:
                    raise ValueError(
                        "Ledger reversal produced negative cash."
                    )
                index -= 1

            points.append({
                "measured_at": at.isoformat(),
                "cash_balance_usd": str(cash),
                "positions": [
                    {
                        "asset_id": asset_id,
                        "quantity": str(quantity),
                    }
                    for asset_id, quantity in sorted(quantities.items())
                    if quantity > 0
                ],
            })

    return {
        "methodology": "reverse_recorded_transactions_v1",
        "status": "reconstructed" if points else "insufficient_data",
        "snapshot_at": cutoff.isoformat(),
        "earliest_surviving_transaction_at": (
            earliest.isoformat() if earliest is not None else None
        ),
        "points": list(reversed(points)),
        "excluded_points": excluded,
        "external_flow_times": [
            row["created_at"]
            for row in effects
            if row["external_cash_flow"]
        ],
        "historical_completeness_verified": False,
        "limitations": [
            "Resets delete history without retaining a reset record.",
            "Missing or edited ledger entries may remain undetectable.",
            "Reconstructed balances still require price-quality checks.",
        ],
        "database_writes": False,
        "allocation_authority": False,
        "live_capital_authority": False,
    }
