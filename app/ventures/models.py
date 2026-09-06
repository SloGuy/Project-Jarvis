from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class OpportunityStatus(str, Enum):
    DISCOVERED = "discovered"
    SCREENING = "screening"
    RESEARCH = "research"
    UNDERWRITING = "underwriting"
    COMMITTEE = "committee"
    WATCH = "watch"
    REJECTED = "rejected"
    APPROVED = "approved"


class BusinessType(str, Enum):
    SAAS = "saas"
    MICRO_SAAS = "micro_saas"
    SUBSCRIPTION_DATA = "subscription_data"
    LEAD_GENERATION = "lead_generation"
    DIGITAL_CONTENT = "digital_content"
    PRODUCTIZED_SERVICE = "productized_service"
    OTHER = "other"


@dataclass(frozen=True)
class VenturesOpportunity:
    opportunity_id: str
    name: str
    business_type: BusinessType
    asking_price_usd: float
    annual_revenue_usd: Optional[float]
    annual_sde_usd: Optional[float]
    owner_hours_per_week: Optional[float]
    source: Optional[str]
    source_url: Optional[str]
    notes: Optional[str]
    status: OpportunityStatus
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict:
        data = asdict(self)
        data["business_type"] = self.business_type.value
        data["status"] = self.status.value
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "VenturesOpportunity":
        return cls(
            opportunity_id=data["opportunity_id"],
            name=data["name"],
            business_type=BusinessType(data["business_type"]),
            asking_price_usd=float(data["asking_price_usd"]),
            annual_revenue_usd=(
                float(data["annual_revenue_usd"])
                if data.get("annual_revenue_usd") is not None
                else None
            ),
            annual_sde_usd=(
                float(data["annual_sde_usd"])
                if data.get("annual_sde_usd") is not None
                else None
            ),
            owner_hours_per_week=(
                float(data["owner_hours_per_week"])
                if data.get("owner_hours_per_week") is not None
                else None
            ),
            source=data.get("source"),
            source_url=data.get("source_url"),
            notes=data.get("notes"),
            status=OpportunityStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
