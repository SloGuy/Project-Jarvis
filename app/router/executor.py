from typing import Any

from app.router.models import RouteDecision
from app.router.registry import CAPABILITY_BY_NAME


def execute_direct_route(decision: RouteDecision) -> Any:
    capability = CAPABILITY_BY_NAME.get(decision.intent)

    if capability is None:
        raise ValueError(
            f"Unsupported direct-route intent: {decision.intent}"
        )

    data = capability.execute(**decision.parameters)

    return {
        "status": "success",
        "summary": capability.format_response(
            data,
            **decision.parameters,
        ),
        "data": data,
    }
