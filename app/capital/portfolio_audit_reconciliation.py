"""Compare audit row-image chains with current accounting rows.

Matching images do not prove complete history or transaction commit order.
No database access or accounting mutations are performed.
"""
import json
from decimal import Decimal

from app.capital.portfolio_audit_events import SOURCES


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate current-row JSON field.")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Nonfinite current-row JSON number.")


def _current_image(row):
    image = json.loads(
        row["row_json"],
        parse_float=Decimal,
        parse_constant=_invalid_constant,
        object_pairs_hook=_unique_fields,
    )
    if not isinstance(image, dict):
        raise ValueError("Current row must be a JSON object.")
    identifier = image.get("id")
    if type(identifier) is not int or identifier <= 0:
        raise ValueError("Invalid current-row identity.")
    if identifier != row["source_row_id"]:
        raise ValueError("Current-row identity mismatch.")
    return image


def reconcile_audit_snapshot(snapshot):
    """Consume a snapshot returned by read_portfolio_audit.

    Chains follow image equality, never event-ID or timestamp ordering.
    Repeated indistinguishable states are reported as ambiguous.
    """
    current = {}
    for source in SOURCES:
        for row in snapshot["current_rows"][source]:
            image = _current_image(row)
            key = (source, image["id"])
            if key in current:
                raise ValueError("Duplicate current row.")
            current[key] = image

    grouped = {}
    event_ids = set()
    for event in snapshot["events"]:
        identifier = event["event_id"]
        if identifier in event_ids:
            raise ValueError("Duplicate audit event ID.")
        event_ids.add(identifier)
        source = event["source_table"]
        if source not in SOURCES:
            raise ValueError("Unsupported audit source.")
        key = (source, event["source_row_id"])
        grouped.setdefault(key, []).append(event)

    issues = []
    matched = 0
    for key in sorted(set(current) | set(grouped)):
        events = grouped.get(key, [])
        reason = None
        baseline = [e for e in events if e["operation"] == "BASELINE"]
        changes = [e for e in events if e["operation"] != "BASELINE"]

        if len(baseline) > 1:
            reason = "duplicate_baseline"
        elif any(
            image is not None and image.get("id") != key[1]
            for event in events
            for image in (event["before"], event["after"])
        ):
            reason = "row_identity_change_requires_separate_handling"

        state = baseline[0]["after"] if baseline else None
        remaining = list(changes)

        while remaining and reason is None:
            possible = [
                event for event in remaining
                if event["before"] == state
            ]
            if not possible:
                reason = "broken_row_image_chain"
            elif len(possible) != 1:
                reason = "ambiguous_row_image_chain"
            else:
                selected = possible[0]
                state = selected["after"]
                remaining.remove(selected)

        if reason is None and state != current.get(key):
            reason = "final_image_differs_from_current_row"

        if reason is None:
            matched += 1
        else:
            issues.append({
                "source_table": key[0],
                "source_row_id": key[1],
                "reason": reason,
            })

    return {
        "installation_id": snapshot["installation"]["installation_id"],
        "sampled_at": snapshot["sampled_at"],
        "status": "row_images_match" if not issues else "unresolved",
        "matched_row_count": matched,
        "issues": issues,
        "scope": "audit_row_image_chains_against_same_snapshot_current_rows",
        "historical_completeness_verified": False,
        "commit_order_verified": False,
        "database_writes": False,
        "execution_authorized": False,
    }
