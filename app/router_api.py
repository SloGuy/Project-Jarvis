from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.router_service import route_message


router = APIRouter(
    prefix="/router",
    tags=["router"],
)


class RouterRequest(BaseModel):
    message: str = Field(
        min_length=1,
        description="The user message Jarvis should classify and route.",
    )


@router.post("")
def router_endpoint(request: RouterRequest) -> dict:
    return route_message(request.message)
