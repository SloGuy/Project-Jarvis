from app.router.models import RouteDecision, RouteType
from app.router.registry import CAPABILITIES


def normalize_message(message: str) -> str:
    return " ".join(message.lower().strip().split())


def classify_message(message: str) -> RouteDecision:
    normalized = normalize_message(message)

    for capability in CAPABILITIES:
        parameters = capability.match(normalized)

        if parameters is not None:
            return RouteDecision(
                route_type=RouteType.DIRECT,
                intent=capability.name,
                endpoint=capability.endpoint,
                confidence=1.0,
                parameters=parameters,
            )

    return RouteDecision(
        route_type=RouteType.LLM,
        intent="general_reasoning",
        endpoint=None,
        confidence=0.0,
        parameters={},
    )
