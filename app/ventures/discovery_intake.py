from __future__ import annotations

from math import isfinite
from urllib.parse import urlencode

from app.ventures.discovery_empire_flippers import ENDPOINT
from app.ventures.models import BusinessType
from app.ventures.opportunities import create_opportunity


MONETIZATION_TYPES = {
    "SaaS": BusinessType.SAAS,
    "Lead Generation": BusinessType.LEAD_GENERATION,
    "Display Advertising": BusinessType.DIGITAL_CONTENT,
    "Affiliate": BusinessType.DIGITAL_CONTENT,
    "Amazon Associates": BusinessType.DIGITAL_CONTENT,
    "Amazon KDP": BusinessType.DIGITAL_CONTENT,
    "Info Product": BusinessType.DIGITAL_CONTENT,
}


def _number(value, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric.")

    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be numeric.") from exc

    if not isfinite(result) or result < 0:
        raise ValueError(f"{field} must be finite and nonnegative.")

    return result


def _business_type(listing: dict) -> BusinessType:
    monetizations = listing.get("monetizations")
    if not isinstance(monetizations, list) or not monetizations:
        return BusinessType.OTHER

    mapped = set()
    for item in monetizations:
        if not isinstance(item, dict):
            return BusinessType.OTHER

        category = MONETIZATION_TYPES.get(item.get("monetization"))
        if category is None:
            return BusinessType.OTHER
        mapped.add(category)

    if len(mapped) == 1:
        return mapped.pop()

    return BusinessType.OTHER


def import_listing(listing: dict) -> dict:
    source_id = listing.get("id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("Listing source ID is required.")

    def skipped(reason):
        return {
            "source_id": source_id,
            "outcome": "skipped",
            "reason": reason,
        }

    if listing.get("listing_status") != "For Sale":
        return skipped("Listing is not currently marked For Sale.")

    if listing.get("unpriced") is True:
        return skipped("Listing is explicitly unpriced.")

    listing_number = listing.get("listing_number")
    if type(listing_number) is not int or listing_number <= 0:
        return skipped("Valid listing number is missing.")

    title = listing.get("public_title")
    if not isinstance(title, str) or not title.strip():
        return skipped("Public listing title is missing.")

    try:
        price = _number(listing.get("listing_price"), "listing_price")
    except ValueError as exc:
        return skipped(str(exc))

    if price is None or price <= 0:
        return skipped("A positive asking price is required.")

    warnings = []
    try:
        hours = _number(
            listing.get("hours_worked_per_week"),
            "hours_worked_per_week",
        )
    except ValueError as exc:
        hours = None
        warnings.append(str(exc))

    source = f"empire_flippers:{source_id}"
    source_url = ENDPOINT + "?" + urlencode({
        "listing_number": listing_number,
    })

    notes = (
        "Automatically discovered from Empire Flippers. "
        "Listing claims are unverified. "
        "Original financial values and their periods are retained "
        "in discovery snapshots. Monthly averages have not been "
        "substituted for annual revenue or SDE. "
        "Business type is a provisional source-category mapping."
    )

    opportunity = create_opportunity(
        name=title.strip(),
        business_type=_business_type(listing),
        asking_price_usd=price,
        annual_revenue_usd=None,
        annual_sde_usd=None,
        owner_hours_per_week=hours,
        source=source,
        source_url=source_url,
        notes=notes,
        deduplicate_source=True,
    )

    return {
        "source_id": source_id,
        "listing_number": listing_number,
        "source_url": source_url,
        "opportunity_id": opportunity.opportunity_id,
        "outcome": "linked",
        "warnings": warnings,
    }
