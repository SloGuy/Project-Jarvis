from fastapi import FastAPI

from app.health import get_system_health
from app.docker_status import get_docker_status

app = FastAPI(
    title="Jarvis Core",
    version="2.0.0",
)


@app.get("/")
def root():
    return {
        "name": "Jarvis Core",
        "version": "2.0.0",
        "status": "online",
    }


@app.get("/health")
def health():
    return get_system_health()


@app.get("/docker")
def docker():
    return get_docker_status()


@app.get("/overview")
def overview():
    return {
        "system": get_system_health(),
        "docker": get_docker_status(),
    }