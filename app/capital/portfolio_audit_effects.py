"""Compare audited cash/quantity changes with inserted ledger effects.

This checks amounts within database transactions, not commit timing,
historical completeness, cost basis, or trading authorization.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext

from app.capital.portfolio_transaction_effects import transaction_effect


def _number(value):
    if isinstance(value, bool):
        raise ValueError("Expected a finite accounting number.")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("Invalid accounting number.") from error
    if not number.is_finite() or number < 0:
        raise ValueError("Expected a nonnegative finite accounting number.")
    return number


def _id(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Expected a positive identifier.")
    return value


def _nonzero(values):
    return {key: value for key, value in values.items() if value != 0}


def _assess_group(events):
    actual_cash = defaultdict(Decimal)
    expected_cash = defaultdict(Decimal)
    actual_quantity = defaultdict(Decimal)
    expected_quantity = defaultdict(Decimal)
    ledger_ids = set()
    external_flows = []
    reasons = set()

    with localcontext() as context:
        context.prec = 60

        for event in events:
            source = event["source_table"]
            operation = event["operation"]
            before = event["before"]
            after = event["after"]

            if before is not None and after is not None:
                if before["id"] != after["id"]:
                    reasons.add("row_identity_changed")
                    continue

            if source == "portfolios":
                if operation != "UPDATE":
                    reasons.add("portfolio_creation_or_deletion")
                    continue
                if (
                    before["portfolio_type"] != "paper"
                    or after["portfolio_type"] != "paper"
                ):
                    reasons.add("nonpaper_portfolio")
                    continue
                portfolio_id = _id(after["id"])
                actual_cash[portfolio_id] += (
                    _number(after["cash_balance_usd"])
                    - _number(before["cash_balance_usd"])
                )

            elif source == "portfolio_positions":
                if operation not in {"INSERT", "UPDATE", "DELETE"}:
                    reasons.add("unsupported_position_operation")
                    continue
                old_key = (
                    (_id(before["portfolio_id"]), _id(before["asset_id"]))
                    if before is not None else None
                )
                new_key = (
                    (_id(after["portfolio_id"]), _id(after["asset_id"]))
                    if after is not None else None
                )
                if old_key is not None and new_key is not None:
                    if old_key != new_key:
                        reasons.add("position_identity_changed")
                        continue
                if before is not None:
                    actual_quantity[old_key] -= _number(before["quantity"])
                if after is not None:
                    actual_quantity[new_key] += _number(after["quantity"])

            elif source == "portfolio_transactions":
                if operation != "INSERT":
                    reasons.add("ledger_update_or_deletion")
                    continue
                ledger_id = _id(after["id"])
                if ledger_id in ledger_ids:
                    raise ValueError("Duplicate inserted ledger ID.")
                ledger_ids.add(ledger_id)
                portfolio_id = _id(after["portfolio_id"])
                effect = transaction_effect(after)
                expected_cash[portfolio_id] += Decimal(
                    effect["cash_delta_usd"]
                )
                if effect["asset_id"] is not None:
                    key = (portfolio_id, effect["asset_id"])
                    expected_quantity[key] += Decimal(
                        effect["quantity_delta"]
                    )
                if effect["external_cash_flow"]:
                    external_flows.append({
                        "portfolio_id": portfolio_id,
                        "transaction_id": ledger_id,
                        "transaction_type": effect["transaction_type"],
                        "cash_delta_usd": effect["cash_delta_usd"],
                        "ledger_created_at": effect["created_at"],
                        "commit_time_verified": False,
                    })
            else:
                reasons.add("unsupported_source")

        if _nonzero(actual_cash) != _nonzero(expected_cash):
            reasons.add("cash_delta_mismatch")
        if _nonzero(actual_quantity) != _nonzero(expected_quantity):
            reasons.add("quantity_delta_mismatch")

    return {
        "status": "amounts_match" if not reasons else "unresolved",
        "reasons": sorted(reasons),
        "event_count": len(events),
        "inserted_ledger_count": len(ledger_ids),
        "external_flows": external_flows,
    }


def assess_audit_effects(snapshot):
    """Consume decoded events from the consistent audit snapshot reader.

    Baseline events are excluded. Matching net amounts do not verify
    intermediate states or replace row-image reconciliation.
    """
    groups = defaultdict(list)
    seen = set()
    for event in snapshot["events"]:
        event_id = _id(event["event_id"])
        if event_id in seen:
            raise ValueError("Duplicate audit event ID.")
        seen.add(event_id)
        if event["operation"] != "BASELINE":
            groups[_id(event["database_transaction_id"])].append(event)

    results = []
    for transaction_id, events in sorted(groups.items()):
        try:
            result = _assess_group(events)
        except (ValueError, KeyError, TypeError, InvalidOperation) as error:
            result = {
                "status": "unresolved",
                "reasons": ["invalid_or_unsupported_accounting_evidence"],
                "error_type": type(error).__name__,
                "event_count": len(events),
                "external_flows": [],
            }
        results.append({
            "database_transaction_id": transaction_id,
            **result,
        })

    return {
        "installation_id": snapshot["installation"]["installation_id"],
        "sampled_at": snapshot["sampled_at"],
        "status": (
            "no_changes" if not results
            else "amounts_match"
            if all(row["status"] == "amounts_match" for row in results)
            else "unresolved"
        ),
        "transaction_groups": results,
        "scope": "net_cash_and_quantity_effects_per_database_transaction",
        "historical_completeness_verified": False,
        "commit_order_verified": False,
        "database_writes": False,
        "execution_authorized": False,
    }
