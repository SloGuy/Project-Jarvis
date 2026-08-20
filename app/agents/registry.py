from app.agents.models import (
    AgentDefinition,
    AgentPermission,
    AgentStatus,
)


# Explicitly registered Jarvis agents in the agent registry
# Orchestrator workflow test.
AGENTS = (
    AgentDefinition(
        agent_id="engineering.software_engineer",
        name="Software Engineer",
        organization="Jarvis Engineering",
        role="software_engineer",
        description=(
            "Builds, debugs, tests, and improves Jarvis code "
            "within explicitly granted permissions."
        ),
        capabilities=(
            "inspect_code",
            "search_code",
            "propose_code_changes",
            "run_tests",
            "inspect_git_diff",
        ),
        permissions=(
            AgentPermission.READ,
            AgentPermission.EXECUTE,
            AgentPermission.APPROVAL_REQUIRED,
        ),
        status=AgentStatus.IDLE,
        model=None,
    ),
    AgentDefinition(
        agent_id="engineering.reviewer",
        name="Engineering Reviewer",
        organization="Jarvis Engineering",
        role="reviewer",
        description=(
            "Reviews proposed Jarvis code changes, "
            "verification results, and implementation quality "
            "before changes are approved for application."
        ),
        capabilities=(
            "inspect_code",
            "inspect_git_diff",
            "run_tests",
            "review_patch",
        ),
        permissions=(
            AgentPermission.READ,
            AgentPermission.EXECUTE,
        ),
        status=AgentStatus.IDLE,
        model=None,
    ),
)


def get_agents() -> tuple[AgentDefinition, ...]:
    return AGENTS


def get_agent(
    agent_id: str,
) -> AgentDefinition | None:
    normalized = agent_id.strip().lower()

    for agent in AGENTS:
        if agent.agent_id.lower() == normalized:
            return agent

    return None


def get_agents_by_organization(
    organization: str,
) -> tuple[AgentDefinition, ...]:
    normalized = (
        organization
        .strip()
        .lower()
    )

    return tuple(
        agent
        for agent in AGENTS
        if (
            agent.organization
            .lower()
            == normalized
        )
    )

# Exposes a read-only snapshot of the registered Jarvis agents.
# This is intended for external systems to read the current state of the agent registry.
# No modifications should be made to the data through this interface.
def get_agent_registry_snapshot() -> dict:
    agents = get_agents()

    return {
        "status": "success",
        "agent_count": len(agents),
        "agents": [
            {
                "agent_id": agent.agent_id,
                "name": agent.name,
                "organization": agent.organization,
                "role": agent.role,
                "description": agent.description,
                "capabilities": list(
                    agent.capabilities
                ),
                "permissions": [
                    permission.value
                    for permission
                    in agent.permissions
                ],
                "status": agent.status.value,
                "model": agent.model,
            }
            for agent in agents
        ],
    }
