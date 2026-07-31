from fastapi import FastAPI, Query

from app.docker_status import get_docker_status
from app.health import get_system_health
from app.update_status import get_update_status
from app.service_status import get_service_status
from app.market_status import get_market_status


app = FastAPI(
    title="Jarvis Core",
    version="2.1.0",
    description="Local system, Docker, and update intelligence API for Jarvis.",
)


@app.get("/")
def root():
    return {
        "name": "Jarvis Core",
        "version": app.version,
        "status": "online",
        "endpoints": {
            "health": "/health",
            "docker": "/docker",
            "updates": "/updates",
            "overview": "/overview",
        },
    }


@app.get("/health")
def health():
    return get_system_health()


@app.get("/docker")
def docker():
    return get_docker_status()


@app.get("/updates")
def updates(
    force_refresh: bool = Query(
        default=False,
        description="Bypass the update-status cache and run a fresh check.",
    )
):
    return get_update_status(force_refresh=force_refresh)


@app.get("/services")
def services():
    return get_service_status()


@app.get("/overview")
def overview(
    include_updates: bool = Query(
        default=True,
        description="Include Ubuntu update intelligence in the overview.",
    ),
    force_update_refresh: bool = Query(
        default=False,
        description="Bypass the update-status cache when updates are included.",
    ),
):
    response = {
        "system": get_system_health(),
        "docker": get_docker_status(),
        "services": get_service_status(),
    }

    if include_updates:
        response["updates"] = get_update_status(
            force_refresh=force_update_refresh
        )

    return response



@app.get("/market")
def market():
    return get_market_status()
