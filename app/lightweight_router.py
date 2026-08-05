from dataclasses import dataclass
from enum import Enum
import re


class RouteType(str, Enum):
    DIRECT = "direct"
    LLM = "llm"


@dataclass(frozen=True)
class RouteDecision:
    route_type: RouteType
    intent: str
    endpoint: str | None = None
    confidence: float = 0.0


DIRECT_ROUTES = {
    "overview": {
        "endpoint": "/overview/summary",
        "patterns": (
            r"\b(system|server|jarvis)\s+overview\b",
            r"\boverview\s+of\s+(the\s+)?(system|server|jarvis)\b",
            r"\bhow\s+is\s+(the\s+)?(system|server|jarvis)\s+doing\b",
            r"\bcurrent\s+(system|server|jarvis)\s+status\b",
        ),
    },
    "health": {
        "endpoint": "/health",
        "patterns": (
            r"\bsystem\s+health\b",
            r"\bserver\s+health\b",
            r"\bhealth\s+status\b",
            r"\bcpu\s+(usage|temperature|status)\b",
            r"\bmemory\s+(usage|status)\b",
            r"\bdisk\s+(usage|status)\b",
        ),
    },
    "docker": {
        "endpoint": "/docker",
        "patterns": (
            r"\bdocker\s+(status|health|containers?)\b",
            r"\bcontainer\s+status\b",
            r"\bcontainers?\s+(running|healthy)\b",
        ),
    },
    "services": {
        "endpoint": "/services",
        "patterns": (
            r"\bservice\s+status\b",
            r"\bservices\s+(running|healthy)\b",
            r"\bjarvis\s+services\b",
        ),
    },
    "updates": {
        "endpoint": "/updates",
        "patterns": (
            r"\bubuntu\s+updates?\b",
            r"\bsystem\s+updates?\b",
            r"\bupdates?\s+available\b",
            r"\breboot\s+required\b",
        ),
    },
}


def classify_message(message: str) -> RouteDecision:
    normalized = " ".join(message.lower().strip().split())

    for intent, route in DIRECT_ROUTES.items():
        for pattern in route["patterns"]:
            if re.search(pattern, normalized):
                return RouteDecision(
                    route_type=RouteType.DIRECT,
                    intent=intent,
                    endpoint=route["endpoint"],
                    confidence=1.0,
                )

    return RouteDecision(
        route_type=RouteType.LLM,
        intent="general_reasoning",
        endpoint=None,
        confidence=0.0,
    )
