from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Iterable
import fcntl
from functools import wraps

from app.ventures.models import (
    BusinessType,
    OpportunityStatus,
    VenturesOpportunity,
    utc_now,
)
from app.ventures.lifecycle import (
    require_valid_transition,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

VENTURES_STATE_DIRECTORY = (
    PROJECT_ROOT
    / "state"
    / "ventures"
)

OPPORTUNITIES_FILE = (
    VENTURES_STATE_DIRECTORY
    / "opportunities.json"
)


def _ensure_state_directory() -> None:
    VENTURES_STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )


def _load_raw_opportunities() -> list[dict]:
    _ensure_state_directory()

    if not OPPORTUNITIES_FILE.exists():
        return []

    with OPPORTUNITIES_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError(
            "Ventures opportunities state must be a JSON list."
        )

    return data


def _save_opportunities(
    opportunities: Iterable[VenturesOpportunity],
) -> None:
    _ensure_state_directory()

    payload = [
        opportunity.to_dict()
        for opportunity in opportunities
    ]

    temporary_file = (
        OPPORTUNITIES_FILE.with_suffix(".tmp")
    )

    with temporary_file.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            payload,
            file,
            indent=2,
            sort_keys=True,
        )

    temporary_file.replace(
        OPPORTUNITIES_FILE
    )


def list_opportunities() -> list[VenturesOpportunity]:
    return [
        VenturesOpportunity.from_dict(item)
        for item in _load_raw_opportunities()
    ]


def get_opportunity(
    opportunity_id: str,
) -> VenturesOpportunity | None:
    for opportunity in list_opportunities():
        if opportunity.opportunity_id == opportunity_id:
            return opportunity

    return None


def _locked_opportunity_write(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        _ensure_state_directory()
        lock_file = OPPORTUNITIES_FILE.with_suffix(".lock")

        with lock_file.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                return function(*args, **kwargs)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    return wrapped


@_locked_opportunity_write
def create_opportunity(
    *,
    name: str,
    business_type: BusinessType,
    asking_price_usd: float,
    annual_revenue_usd: float | None = None,
    annual_sde_usd: float | None = None,
    owner_hours_per_week: float | None = None,
    source: str | None = None,
    source_url: str | None = None,
    notes: str | None = None,
    deduplicate_source: bool = False,
) -> VenturesOpportunity:
    opportunities = list_opportunities()

    if deduplicate_source:
        if not source or not source_url:
            raise ValueError(
                "Source and source URL are required for deduplication."
            )

        for existing in opportunities:
            if (
                existing.source == source
                and existing.source_url == source_url
            ):
                return existing

    now = utc_now()
    opportunity = VenturesOpportunity(
        opportunity_id=f"venture_{uuid.uuid4().hex[:12]}",
        name=name.strip(),
        business_type=business_type,
        asking_price_usd=float(asking_price_usd),
        annual_revenue_usd=annual_revenue_usd,
        annual_sde_usd=annual_sde_usd,
        owner_hours_per_week=owner_hours_per_week,
        source=source,
        source_url=source_url,
        notes=notes,
        status=OpportunityStatus.DISCOVERED,
        created_at=now,
        updated_at=now,
    )

    opportunities.append(opportunity)
    _save_opportunities(opportunities)
    return opportunity


@_locked_opportunity_write
def update_opportunity_status(
    opportunity_id: str,
    status: OpportunityStatus,
) -> VenturesOpportunity:
    opportunities = list_opportunities()

    updated_opportunity = None
    updated_list = []

    for opportunity in opportunities:
        if opportunity.opportunity_id == opportunity_id:
            require_valid_transition(
                opportunity.status,
                status,
            )

            opportunity = replace(
                opportunity,
                status=status,
                updated_at=utc_now(),
            )
            updated_opportunity = opportunity

        updated_list.append(opportunity)

    if updated_opportunity is None:
        raise KeyError(
            f"Ventures opportunity not found: "
            f"{opportunity_id}"
        )

    _save_opportunities(updated_list)

    return updated_opportunity
