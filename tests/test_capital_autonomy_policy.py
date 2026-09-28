"""Tests for Capital agent role, pause, and paper-only boundaries."""

import unittest

from app.agents.capital_registry import (
    LIFECYCLE_AGENT_ID,
    RESEARCH_AGENT_ID,
    VALIDATION_AGENT_ID,
    get_capital_agents,
)
from app.capital.autonomy_policy import (
    CapitalOperatingPolicy,
    CapitalPolicyError,
    authorize_capital_action,
)


class CapitalAutonomyPolicyTests(unittest.TestCase):
    def authorize(self, agent, action, **overrides):
        arguments = {
            "agent_id": agent,
            "action": action,
            "policy": CapitalOperatingPolicy(),
            "execution_mode": "paper",
        }
        arguments.update(overrides)
        return authorize_capital_action(**arguments)

    def test_registered_capabilities_pass_role_check(self):
        for agent in get_capital_agents():
            for action in agent.capabilities:
                with self.subTest(agent=agent.agent_id, action=action):
                    result = self.authorize(agent.agent_id, action)
                    self.assertEqual(result["role_and_mode_check"], "passed")
                    self.assertTrue(
                        result["evidence_and_budget_checks_required"]
                    )
                    self.assertFalse(result["live_capital_authorized"])

    def test_research_cannot_promote(self):
        with self.assertRaises(CapitalPolicyError):
            self.authorize(RESEARCH_AGENT_ID, "capital.promote_paper")

    def test_validation_cannot_allocate(self):
        with self.assertRaises(CapitalPolicyError):
            self.authorize(VALIDATION_AGENT_ID, "capital.allocate_paper")

    def test_lifecycle_cannot_register_validation(self):
        with self.assertRaises(CapitalPolicyError):
            self.authorize(
                LIFECYCLE_AGENT_ID, "capital.register_validation"
            )

    def test_engineering_agent_is_not_capital_agent(self):
        with self.assertRaises(CapitalPolicyError):
            self.authorize(
                "engineering.software_engineer",
                "capital.promote_paper",
            )

    def test_arbitrary_commands_are_rejected(self):
        for action in ("run_tests", "shell", "capital.execute_code", ""):
            with self.subTest(action=action):
                with self.assertRaises(CapitalPolicyError):
                    self.authorize(LIFECYCLE_AGENT_ID, action)

    def test_live_dispatch_is_rejected(self):
        with self.assertRaises(CapitalPolicyError):
            self.authorize(
                LIFECYCLE_AGENT_ID,
                "capital.promote_paper",
                execution_mode="live",
            )

    def test_live_policy_cannot_be_constructed(self):
        with self.assertRaises(ValueError):
            CapitalOperatingPolicy(execution_mode="live")

    def test_invalid_flags_are_rejected(self):
        for field in ("enabled", "paused"):
            for value in (0, 1, "true", None):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        CapitalOperatingPolicy(**{field: value})

    def test_agent_supplied_dictionary_is_not_policy(self):
        with self.assertRaises(CapitalPolicyError):
            self.authorize(
                LIFECYCLE_AGENT_ID,
                "capital.promote_paper",
                policy={"enabled": True, "execution_mode": "paper"},
            )

    def test_disabled_policy_blocks_all_actions(self):
        policy = CapitalOperatingPolicy(enabled=False)
        for agent in get_capital_agents():
            for action in agent.capabilities:
                with self.subTest(agent=agent.agent_id, action=action):
                    with self.assertRaises(CapitalPolicyError):
                        self.authorize(
                            agent.agent_id, action, policy=policy
                        )

    def test_pause_blocks_new_work_and_increases(self):
        policy = CapitalOperatingPolicy(paused=True)
        actions = (
            (RESEARCH_AGENT_ID, "capital.propose_research"),
            (VALIDATION_AGENT_ID, "capital.run_validation"),
            (LIFECYCLE_AGENT_ID, "capital.promote_paper"),
            (LIFECYCLE_AGENT_ID, "capital.allocate_paper"),
        )
        for agent, action in actions:
            with self.subTest(action=action):
                with self.assertRaises(CapitalPolicyError):
                    self.authorize(agent, action, policy=policy)

    def test_pause_allows_risk_reduction(self):
        policy = CapitalOperatingPolicy(paused=True)
        for action in (
            "capital.pause_paper",
            "capital.demote_paper",
            "capital.retire_paper",
        ):
            with self.subTest(action=action):
                result = self.authorize(
                    LIFECYCLE_AGENT_ID, action, policy=policy
                )
                self.assertEqual(result["role_and_mode_check"], "passed")

    def test_pause_allows_inspection_and_learning(self):
        policy = CapitalOperatingPolicy(paused=True)
        for agent in get_capital_agents():
            for action in (
                "capital.inspect_evidence",
                "capital.record_learning",
            ):
                with self.subTest(agent=agent.agent_id, action=action):
                    result = self.authorize(
                        agent.agent_id, action, policy=policy
                    )
                    self.assertFalse(result["live_capital_authorized"])

    def test_pause_does_not_expand_agent_permissions(self):
        with self.assertRaises(CapitalPolicyError):
            self.authorize(
                RESEARCH_AGENT_ID,
                "capital.retire_paper",
                policy=CapitalOperatingPolicy(paused=True),
            )


if __name__ == "__main__":
    unittest.main()
