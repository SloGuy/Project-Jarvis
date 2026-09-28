"""Capital agent definitions for autonomous research and paper operations.

Capabilities are declarations. Execution handlers must independently
enforce policy, evidence, resource limits, and paper-only operation.
"""

from app.agents.models import (
    AgentDefinition,
    AgentPermission,
    AgentStatus,
)


RESEARCH_AGENT_ID = "capital.research"
VALIDATION_AGENT_ID = "capital.validation"
LIFECYCLE_AGENT_ID = "capital.lifecycle"


def _definition(*, agent_id, name, role, description, capabilities):
    return AgentDefinition(
        agent_id=agent_id,
        name=name,
        organization="Jarvis Capital",
        role=role,
        description=description,
        capabilities=capabilities,
        permissions=(
            AgentPermission.READ,
            AgentPermission.EXECUTE,
            AgentPermission.WRITE,
        ),
        status=AgentStatus.IDLE,
        model=None,
    )


CAPITAL_AGENTS = (
    _definition(
        agent_id=RESEARCH_AGENT_ID,
        name="Capital Research",
        role="capital_research",
        description=(
            "Selects research questions, proposes and revises hypotheses, "
            "and records evidence-backed lessons for subsequent work."
        ),
        capabilities=(
            "capital.inspect_evidence",
            "capital.propose_research",
            "capital.revise_research",
            "capital.record_learning",
        ),
    ),
    _definition(
        agent_id=VALIDATION_AGENT_ID,
        name="Capital Validation",
        role="capital_validation",
        description=(
            "Registers permitted experiments, runs validation jobs, "
            "and records verified outcomes without changing their criteria."
        ),
        capabilities=(
            "capital.inspect_evidence",
            "capital.register_validation",
            "capital.collect_validation",
            "capital.run_validation",
            "capital.assess_validation",
            "capital.record_learning",
        ),
    ),
    _definition(
        agent_id=LIFECYCLE_AGENT_ID,
        name="Capital Paper Lifecycle",
        role="capital_lifecycle",
        description=(
            "Applies operating policy to create, promote, allocate, "
            "pause, demote, and retire paper experiments."
        ),
        capabilities=(
            "capital.inspect_evidence",
            "capital.review_research",
            "capital.create_paper_experiment",
            "capital.promote_paper",
            "capital.allocate_paper",
            "capital.pause_paper",
            "capital.demote_paper",
            "capital.retire_paper",
            "capital.record_learning",
        ),
    ),
)


def get_capital_agents() -> tuple[AgentDefinition, ...]:
    return CAPITAL_AGENTS
