from typing import Any

from app.docker_status import get_docker_status
from app.health import get_system_health
from app.router.capabilities.base import RouterCapability
from app.router.formatter import format_overview_summary
from app.service_status import get_service_status
from app.update_status import get_update_status


class OverviewCapability(RouterCapability):
    name = "overview"
    endpoint = "/overview/summary"

    patterns = (
        r"\b(system|server|jarvis)\s+overview\b",
        r"\boverview\s+of\s+(the\s+)?(system|server|jarvis)\b",
        r"\bhow\s+is\s+(the\s+)?(system|server|jarvis)\s+doing\b",
        r"\bcurrent\s+(system|server|jarvis)\s+status\b",
    )

    def execute(self) -> dict[str, Any]:
        return {
            "system": get_system_health(),
            "docker": get_docker_status(),
            "services": get_service_status(),
            "updates": get_update_status(force_refresh=False),
        }

    def format_response(self, data: Any) -> str:
        if not isinstance(data, dict):
            raise TypeError("Overview capability expected dictionary data.")

        return format_overview_summary(
            system=data.get("system", {}),
            docker=data.get("docker", {}),
            services=data.get("services", {}),
            updates=data.get("updates", {}),
        )
