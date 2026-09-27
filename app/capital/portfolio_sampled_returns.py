"""Hourly sampled indicative returns from captured portfolio evidence.

Actual sampling timestamps are retained. No midnight relabeling,
interpolation, annualization, or trading authority is provided.
"""
from datetime import timedelta
from decimal import Decimal, localcontext

from app.capital.portfolio_daily_returns import utc_timestamp, positive_equity
from app.capital.portfolio_audit_reconciliation import reconcile_audit_snapshot
from app.capital.portfolio_audit_effects import assess_audit_effects


METHOD = "hourly_sampled_indicative_returns_v1"
SLOT_TOLERANCE = timedelta(minutes=5)


def _event_map(audit):
    result = {}
    for event in audit["events"]:
        identifier = event["event_id"]
        if identifier in result:
            raise ValueError("Duplicate audit event.")
        result[identifier] = event
    return result


def _interval_evidence(previous, current):
    old = previous["audit_snapshot"]
    new = current["audit_snapshot"]
    reasons = []
    if old["installation"]["installation_id"] != new["installation"]["installation_id"]:
        return ["audit_installation_changed"], []

    for audit in (old, new):
        if reconcile_audit_snapshot(audit)["status"] != "row_images_match":
            reasons.append("unresolved_row_reconciliation")

    before = _event_map(old)
    after = _event_map(new)
    if any(after.get(key) != value for key, value in before.items()):
        return reasons + ["audit_events_disappeared_or_changed"], []

    added = [event for key, event in after.items() if key not in before]
    if any(event["operation"] == "BASELINE" for event in added):
        reasons.append("baseline_changed")

    old_transactions = {
        event["database_transaction_id"] for event in before.values()
    }
    if any(
        event["database_transaction_id"] in old_transactions
        for event in added
    ):
        reasons.append("transaction_visibility_inconsistent")

    interval_audit = {
        "installation": new["installation"],
        "sampled_at": new["sampled_at"],
        "events": added,
    }
    effects = assess_audit_effects(interval_audit)
    if effects["status"] not in {"amounts_match", "no_changes"}:
        reasons.append("unresolved_interval_accounting")
    flows = [
        flow
        for group in effects["transaction_groups"]
        for flow in group["external_flows"]
    ]
    return sorted(set(reasons)), flows


def _accounts(checkpoint):
    accounts = {}
    for row in checkpoint["valuations"]["portfolios"]:
        identifier = row["portfolio_id"]
        if type(identifier) is not int or identifier <= 0:
            raise ValueError("Invalid portfolio ID.")
        if identifier in accounts:
            raise ValueError("Duplicate portfolio valuation.")
        if utc_timestamp(row["measured_at"]) != utc_timestamp(
            checkpoint["sampled_at"]
        ):
            raise ValueError("Valuation timestamp differs from checkpoint.")
        accounts[identifier] = row
    return accounts


def _bindings(checkpoint):
    result = {}
    for row in checkpoint["resolved_portfolios"]:
        identifier = row["portfolio_id"]
        if identifier in result:
            raise ValueError("Duplicate portfolio binding.")
        result[identifier] = row
    return result


def build_sampled_returns(*, records, as_of):
    """Consume integrity-loaded {record_id, checkpoint} records.

    Select the first capture within five minutes after each UTC hour.
    Consecutive hourly slots are required; actual intervals can be
    55–65 minutes. This is a sampled diagnostic, not daily performance.

    Audit comparisons concern visibility between snapshots, not inferred
    transaction commit timestamps. Completeness remains unverified.
    """
    cutoff = utc_timestamp(as_of)
    slots = {}
    identifiers = set()
    timestamps = set()
    excluded_samples = []

    for record in records:
        identifier = record["record_id"]
        if identifier in identifiers:
            raise ValueError("Duplicate checkpoint record ID.")
        identifiers.add(identifier)
        checkpoint = record["checkpoint"]
        if checkpoint["methodology"] != "prospective_portfolio_evidence_snapshot_v1":
            raise ValueError("Unsupported checkpoint methodology.")
        at = utc_timestamp(checkpoint["sampled_at"])
        if at in timestamps:
            raise ValueError("Ambiguous duplicate checkpoint sampling time.")
        timestamps.add(at)
        if utc_timestamp(checkpoint["audit_snapshot"]["sampled_at"]) != at:
            raise ValueError("Audit timestamp differs from checkpoint.")
        if utc_timestamp(checkpoint["finished_at"]) >= cutoff:
            excluded_samples.append({
                "record_id": identifier, "reason": "not_completed_as_of",
            })
            continue

        slot = at.replace(minute=0, second=0, microsecond=0)
        if at - slot > SLOT_TOLERANCE:
            excluded_samples.append({
                "record_id": identifier, "reason": "outside_hourly_capture_window",
            })
            continue
        existing = slots.get(slot)
        if existing is None or at < existing[0]:
            if existing is not None:
                excluded_samples.append({
                    "record_id": existing[1]["record_id"],
                    "reason": "additional_capture_in_hour",
                })
            slots[slot] = (at, record)
        else:
            excluded_samples.append({
                "record_id": identifier, "reason": "additional_capture_in_hour",
            })

    ordered = sorted(slots.items())
    reports = {}
    for _, (_, record) in ordered:
        for identifier in _accounts(record["checkpoint"]):
            reports.setdefault(identifier, {
                "methodology": METHOD,
                "as_of": cutoff.isoformat(),
                "returns": [],
                "excluded_intervals": [],
            })

    for (old_slot, (start, left)), (new_slot, (end, right)) in zip(
        ordered, ordered[1:]
    ):
        previous, current = left["checkpoint"], right["checkpoint"]
        reasons = []
        if new_slot - old_slot != timedelta(hours=1):
            reasons.append("missing_hourly_checkpoint")
        evidence_reasons, flows = _interval_evidence(previous, current)
        reasons.extend(evidence_reasons)
        old_accounts, new_accounts = _accounts(previous), _accounts(current)
        old_bindings, new_bindings = _bindings(previous), _bindings(current)
        flow_portfolios = {row["portfolio_id"] for row in flows}
        interval = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "start_record_id": left["record_id"],
            "end_record_id": right["record_id"],
        }

        for identifier in sorted(set(old_accounts) | set(new_accounts)):
            blocked = list(reasons)
            before, after = old_accounts.get(identifier), new_accounts.get(identifier)
            if before is None or after is None:
                blocked.append("portfolio_missing_at_endpoint")
            if (
                identifier not in old_bindings
                or identifier not in new_bindings
                or old_bindings[identifier] != new_bindings[identifier]
            ):
                blocked.append("portfolio_binding_changed")
            if identifier in flow_portfolios:
                blocked.append("external_cash_flow")

            start_value = positive_equity(
                before.get("total_value_usd") if before else None
            )
            end_value = positive_equity(
                after.get("total_value_usd") if after else None
            )
            if (
                not before or not after
                or before.get("valuation_status") != "indicative"
                or after.get("valuation_status") != "indicative"
                or start_value is None or end_value is None
            ):
                blocked.append("incomplete_or_invalid_endpoint_valuation")

            report = reports[identifier]
            if blocked:
                report["excluded_intervals"].append({
                    **interval, "reasons": sorted(set(blocked)),
                })
                continue
            with localcontext() as context:
                context.prec = 60
                change = end_value / start_value - Decimal("1")
            report["returns"].append({
                **interval,
                "return_fraction": str(change),
            })

    for report in reports.values():
        report["return_count"] = len(report["returns"])
        report["status"] = (
            "available_indicative" if report["returns"] else "insufficient_data"
        )

    return {
        "methodology": METHOD,
        "as_of": cutoff.isoformat(),
        "selected_checkpoint_count": len(ordered),
        "reports_by_portfolio": reports,
        "excluded_samples": excluded_samples,
        "limitations": [
            "Returns use actual, approximately hourly sampling intervals.",
            "Only consecutive hourly capture slots are joined.",
            "Endpoint values remain indicative provider-price estimates.",
            "Accounting comparisons do not prove complete historical coverage.",
            "External-flow intervals are excluded rather than adjusted.",
        ],
        "historical_completeness_verified": False,
        "execution_authorized": False,
        "allocation_authority": False,
        "live_capital_authority": False,
    }
