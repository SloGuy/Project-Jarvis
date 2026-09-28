"""Action boundaries for the autonomous Capital workflow.

This check grants no standalone credential. Execution handlers must also
verify current evidence, resource limits, and the target portfolio.
"""

from dataclasses import dataclass

from app.agents.capital_registry import get_capital_agents
from app.agents.models import AgentPermission, AgentStatus


POLICY_VERSION = "capital_paper_autonomy_v1"

# These actions remain available while new research and entries are paused.
PAUSED_ACTIONS = frozenset({
    "capital.inspect_evidence",
    "capital.collect_validation",
    "capital.pause_paper",
    "capital.demote_paper",
    "capital.retire_paper",
    "capital.record_learning",
})


class CapitalPolicyError(PermissionError):
    """An action falls outside the Capital operating boundary."""


@dataclass(frozen=True)
class CapitalOperatingPolicy:
    enabled: bool = True
    paused: bool = False
    execution_mode: str = "paper"

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean.")
        if type(self.paused) is not bool:
            raise ValueError("paused must be a boolean.")
        if self.execution_mode != "paper":
            raise ValueError("Capital V3 supports paper operation only.")


def authorize_capital_action(
    *,
    agent_id: str,
    action: str,
    policy: CapitalOperatingPolicy,
    execution_mode: str,
) -> dict:
    """Check role and operating mode immediately before dispatch.

    Callers must load operating policy from operator-controlled state.
    Agent-generated task content must never supply or override that policy.

    A disabled worker performs no actions. A paused worker can inspect,
    record findings, and request risk-reducing lifecycle transitions.
    Pause does not by itself liquidate positions or disable protective exits.
    """
    if not isinstance(policy, CapitalOperatingPolicy):
        raise CapitalPolicyError("A Capital operating policy is required.")

    if execution_mode != "paper" or policy.execution_mode != "paper":
        raise CapitalPolicyError("Live operation is unavailable in Capital V3.")

    if not policy.enabled:
        raise CapitalPolicyError("Capital automation is disabled.")

    if not isinstance(agent_id, str) or not isinstance(action, str):
        raise CapitalPolicyError("Agent and action must be strings.")

    agent = next(
        (
            definition
            for definition in get_capital_agents()
            if definition.agent_id == agent_id
        ),
        None,
    )
    if agent is None:
        raise CapitalPolicyError("Unknown Capital agent.")

    if agent.status not in (AgentStatus.IDLE, AgentStatus.WORKING):
        raise CapitalPolicyError("Capital agent is not available.")

    if AgentPermission.EXECUTE not in agent.permissions:
        raise CapitalPolicyError("Capital agent cannot execute actions.")

    if action not in agent.capabilities:
        raise CapitalPolicyError("Action is outside this agent's capabilities.")

    if policy.paused and action not in PAUSED_ACTIONS:
        raise CapitalPolicyError("Action is blocked while Capital is paused.")

    return {
        "policy_version": POLICY_VERSION,
        "agent_id": agent_id,
        "action": action,
        "execution_mode": "paper",
        "role_and_mode_check": "passed",
        "evidence_and_budget_checks_required": True,
        "live_capital_authorized": False,
    }
