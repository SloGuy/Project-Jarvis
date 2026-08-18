from dataclasses import dataclass, field
from enum import Enum


class AgentStatus(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    PAUSED = "paused"
    ERROR = "error"
    DISABLED = "disabled"


class AgentPermission(str, Enum):
    READ = "read"
    EXECUTE = "execute"
    WRITE = "write"
    APPROVAL_REQUIRED = "approval_required"


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    name: str
    organization: str
    role: str
    description: str

    capabilities: tuple[str, ...] = field(
        default_factory=tuple
    )

    permissions: tuple[AgentPermission, ...] = field(
        default_factory=tuple
    )

    status: AgentStatus = AgentStatus.IDLE

    model: str | None = None
