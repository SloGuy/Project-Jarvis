from typing import Any


def format_overview_summary(
    system: dict[str, Any],
    docker: dict[str, Any],
    services: dict[str, Any],
    updates: dict[str, Any],
) -> str:
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
