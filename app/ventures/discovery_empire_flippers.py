from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ENDPOINT = "https://api.empireflippers.com/api/v1/listings/list"
MAX_RESPONSE_BYTES = 10_000_000


def fetch_listing_page(
    *,
    page: int = 1,
    limit: int = 20,
) -> dict:
    if type(page) is not int or page < 1:
        raise ValueError("Page must be a positive integer.")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100.")

    query = urlencode({
        "page": page,
        "limit": limit,
        "listing_status": "For Sale",
    })
    url = ENDPOINT + "?" + query
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "JarvisVentures/0.1",
        },
    )

    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise ValueError(f"Unexpected HTTP status: {response.status}")
        raw = response.read(MAX_RESPONSE_BYTES + 1)

    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Listing response exceeded size limit.")

    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Expected an API response object.")
    if payload.get("errors"):
        raise ValueError(f"Source returned errors: {payload['errors']}")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("Missing listing response data.")

    listings = data.get("listings")
    if not isinstance(listings, list):
        raise ValueError("Expected a listings array.")

    for key in ("count", "pages", "page", "limit"):
        value = data.get(key)
        if type(value) is not int or value < 0:
            raise ValueError(f"Invalid pagination field: {key}")

    if data["page"] != page or data["limit"] != limit:
        raise ValueError("Source pagination differs from request.")
    if len(listings) > limit:
        raise ValueError("Source returned more listings than requested.")
    if any(not isinstance(item, dict) for item in listings):
        raise ValueError("Invalid listing record.")

    ids = []
    for item in listings:
        source_id = item.get("id")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("Listing is missing its source ID.")
        if item.get("listing_status") != "For Sale":
            raise ValueError("Source returned a listing not marked For Sale.")
        ids.append(source_id)

    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate source IDs within listing page.")

    return {
        "source": "empire_flippers",
        "source_url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "page": data["page"],
        "pages": data["pages"],
        "limit": data["limit"],
        "total_reported": data["count"],
        "listings": listings,
    }


def fetch_all_listings(
    *,
    limit: int = 100,
    max_pages: int = 50,
) -> dict:
    import time

    if type(max_pages) is not int or max_pages < 1:
        raise ValueError("max_pages must be a positive integer.")

    started_at = datetime.now(timezone.utc).isoformat()
    first = fetch_listing_page(page=1, limit=limit)
    total = first["total_reported"]
    pages = first["pages"]

    expected_pages = (total + limit - 1) // limit
    if total == 0:
        if pages not in (0, 1) or first["listings"]:
            raise ValueError("Inconsistent empty-source pagination.")
    elif pages != expected_pages:
        raise ValueError("Source page count does not match listing count.")

    if pages > max_pages:
        raise ValueError(
            f"Source requires {pages} pages; maximum is {max_pages}. "
            "Scan was not completed."
        )

    listings = []
    seen_ids = set()
    page_sources = []

    def collect(result):
        if (
            result["total_reported"] != total
            or result["pages"] != pages
        ):
            raise ValueError(
                "Source pagination changed during scan; retry next run."
            )

        expected_count = min(
            limit,
            max(0, total - (result["page"] - 1) * limit),
        )
        if len(result["listings"]) != expected_count:
            raise ValueError("Source returned an incomplete page.")

        for listing in result["listings"]:
            source_id = listing["id"]
            if source_id in seen_ids:
                raise ValueError(
                    "Listing repeated across pages; scan is inconsistent."
                )
            seen_ids.add(source_id)
            listings.append(listing)

        page_sources.append({
            "page": result["page"],
            "source_url": result["source_url"],
            "fetched_at": result["fetched_at"],
        })

    collect(first)

    for page in range(2, pages + 1):
        time.sleep(1.1)
        collect(fetch_listing_page(page=page, limit=limit))

    if len(listings) != total:
        raise ValueError("Fetched listing count differs from source total.")

    return {
        "source": "empire_flippers",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "total_reported": total,
        "fetched_count": len(listings),
        "pages_fetched": len(page_sources),
        "page_sources": page_sources,
        "listings": listings,
    }


def main():
    result = fetch_listing_page(limit=5)
    preview = []

    for item in result["listings"]:
        preview.append({
            "source_id": item["id"],
            "listing_number": item.get("listing_number"),
            "title": item.get("public_title"),
            "asking_price_usd": item.get("listing_price"),
            "monthly_revenue_reported": (
                item.get("average_monthly_gross_revenue")
            ),
            "monthly_net_profit_reported": (
                item.get("average_monthly_net_profit")
            ),
            "owner_hours_reported": item.get("hours_worked_per_week"),
            "monetizations": item.get("monetizations"),
        })

    print(json.dumps({
        "status": "success",
        "mode": "read_only_page_probe",
        "source": result["source"],
        "source_url": result["source_url"],
        "fetched_at": result["fetched_at"],
        "page": result["page"],
        "pages": result["pages"],
        "total_reported": result["total_reported"],
        "fetched_count": len(preview),
        "state_writes": False,
        "listings": preview,
    }, indent=2))


if __name__ == "__main__":
    main()
