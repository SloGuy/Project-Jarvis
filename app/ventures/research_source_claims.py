from __future__ import annotations

import hashlib
import json

from app.ventures.research_models import (
    EvidenceQuality,
    EvidenceStatus,
    ResearchClaim,
)


EXTRACTOR_VERSION = "empire_flippers_claims_v1"

SOURCE_FIELDS = {
    "listing_price": "Asking price reported by source (USD)",
    "average_monthly_gross_revenue": (
        "Average monthly gross revenue reported by source (USD)"
    ),
    "average_monthly_net_profit": (
        "Average monthly net profit reported by source (USD; not verified SDE)"
    ),
    "average_monthly_expenses": (
        "Average monthly expenses reported by source (USD)"
    ),
    "pricing_period_months": "Pricing period reported by source (months)",
    "hours_worked_per_week": "Weekly owner hours reported by source",
    "summary": "Business description supplied by source",
    "work_required": "Operating work described by source",
    "opportunities": "Growth opportunities described by source",
    "risks": "Risks disclosed by source",
    "reason_for_sale": "Reason for sale supplied by source",
    "seller_support": "Seller support described by source",
    "assets_included": "Included assets described by source",
    "monetizations": "Monetization categories reported by source",
    "niches": "Business niches reported by source",
    "metrics": "Monthly financial history reported by source",
}


def extract_source_claims(
    *,
    listing: dict,
    source_url: str,
    fetched_at: str,
) -> tuple[ResearchClaim, ...]:
    source_id = listing.get("id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("Source listing ID is required.")
    if not source_url.strip() or not fetched_at.strip():
        raise ValueError("Source URL and collection timestamp are required.")

    claims = []

    for field, label in SOURCE_FIELDS.items():
        value = listing.get(field)

        # Empty disclosures do not establish absence of risk or workload.
        if value is None or value == "" or value == [] or value == {}:
            continue

        if isinstance(value, str):
            if not value.strip():
                continue
            rendered = value.strip()
        else:
            rendered = json.dumps(
                value,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )

        # Content-based IDs remain stable across unchanged scans.
        identity = json.dumps(
            {
                "extractor": EXTRACTOR_VERSION,
                "source_id": source_id,
                "field": field,
                "value": value,
            },
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        digest = hashlib.sha256(identity.encode()).hexdigest()

        claims.append(ResearchClaim(
            claim_id=f"source_claim_{digest}",
            category=f"source_{field}",
            claim=f"{label}: {rendered}",
            source=source_url,
            evidence_status=EvidenceStatus.UNVERIFIED,
            evidence_quality=EvidenceQuality.LOW,
            evidence_notes=(
                f"Collected at {fetched_at}. "
                f"Source listing ID: {source_id}. "
                f"Extractor: {EXTRACTOR_VERSION}. "
                "Source disclosure only; not independently verified."
            ),
        ))

    return tuple(claims)
