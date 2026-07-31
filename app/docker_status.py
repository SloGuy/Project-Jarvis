from datetime import datetime, timezone

import docker
from docker.errors import DockerException


def get_docker_status():
    checked_at = datetime.now(timezone.utc).isoformat()

    try:
        client = docker.from_env()
        client.ping()

        containers = []

        for container in client.containers.list(all=True):
            state = container.attrs.get("State", {})
            health = state.get("Health", {}).get("Status")

            containers.append(
                {
                    "name": container.name,
                    "image": container.image.tags[0] if container.image.tags else container.image.short_id,
                    "status": container.status,
                    "running": state.get("Running", False),
                    "health": health or "not_configured",
                    "restart_count": container.attrs.get("RestartCount", 0),
                    "started_at": state.get("StartedAt"),
                    "ports": container.attrs.get("NetworkSettings", {}).get("Ports", {}),
                }
            )

        running = sum(1 for c in containers if c["running"])

        return {
            "status": "healthy",
            "docker_available": True,
            "summary": {
                "total_containers": len(containers),
                "running": running,
                "stopped": len(containers) - running,
            },
            "containers": containers,
            "checked_at": checked_at,
        }

    except DockerException as e:
        return {
            "status": "unavailable",
            "docker_available": False,
            "error": str(e),
            "containers": [],
            "checked_at": checked_at,
        }