from app.router.classifier import classify_message
from app.router.executor import execute_direct_route
from app.router.models import RouteType


def route_message(message: str) -> dict:
    decision = classify_message(message)

    routing = {
        "route_type": decision.route_type.value,
        "intent": decision.intent,
        "endpoint": decision.endpoint,
        "confidence": decision.confidence,
    }

    if decision.route_type == RouteType.DIRECT:
        return {
            "routing": routing,
            "response": execute_direct_route(decision),
        }

    return {
        "routing": routing,
        "response": None,
    }
