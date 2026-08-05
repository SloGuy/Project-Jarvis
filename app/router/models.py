from dataclasses import dataclass
from enum import Enum


class RouteType(str, Enum):
    DIRECT = "direct"
    LLM = "llm"


@dataclass(frozen=True)
class RouteDecision:
    route_type: RouteType
    intent: str
    endpoint: str | None = None
    confidence: float = 0.0
