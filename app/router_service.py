from typing import Any

from app.docker_status import get_docker_status
from app.health import get_system_health
from app.lightweight_router import RouteDecision, RouteType, classify_message
from app.service_status import get_service_status
from app.update_status import get_update_status


def build_overview_summary() -> str:
    system = get_system_health()
    docker = get_docker_status()
    services = get_service_status()
    updates = get_update_status(force_refresh=False)

    cpu = system.get("cpu", {})
    memory = system.get("memory", {})
    disk = system.get("disk", {})
    docker_summary = docker.get("summary", {})
    service_counts = services.get("counts", {})
    ubuntu = updates.get("ubuntu", {})
    reboot = updates.get("reboot", {})

    section_statuses = (
        system.get("status"),
        docker.get("status"),
        services.get("status"),
    )

    overall = (
        "Healthy"
        if all(status == "healthy" for status in section_statuses)
        else "Attention required"
    )

    lines = [
        f"Overall: {overall}",
        (
            f"CPU: {cpu.get('percent_used')}% | "
            f"Memory: {memory.get('percent_used')}% | "
            f"Disk: {disk.get('percent_used')}%"
        ),
        (
            f"Docker: {docker_summary.get('running')}/"
            f"{docker_summary.get('total_containers')} running | "
            f"Services: {service_counts.get('healthy')}/"
            f"{service_counts.get('monitored')} healthy"
        ),
        (
            f"Updates: {ubuntu.get('updates_available')} available, "
            f"{ubuntu.get('security_updates')} security"
        ),
        (
            "Reboot: Required"
            if reboot.get("required")
            else "Reboot: Not required"
        ),
    ]

    return "\n".join(lines)


def execute_direct_route(decision: RouteDecision) -> Any:
    if decision.intent == "overview":
        return {
            "status": "success",
            "summary": build_overview_summary(),
        }

    if decision.intent == "health":
        return get_system_health()

    if decision.intent == "docker":
        return get_docker_status()

    if decision.intent == "services":
        return get_service_status()

    if decision.intent == "updates":
        return get_update_status(force_refresh=False)

    raise ValueError(f"Unsupported direct-route intent: {decision.intent}")


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
