from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RouteType(str, Enum):
    DIRECT = "direct"
    LLM = "llm"


@dataclass(frozen=True)
class RouteDecision:
    route_type: RouteType
    intent: str
    endpoint: str | None = None
    confidence: float = 0.0
    parameters: dict[str, Any] = field(default_factory=dict)
