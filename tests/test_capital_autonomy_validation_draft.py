"""Saved validation draft tests without production database access."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

import test_capital_autonomy_research_draft as research_fixtures
import test_capital_autonomy_validation_registration as registry_fixtures

from app.capital import research_store
from app.capital import validation_registry as registry
from app.capital import autonomy_validation_draft as drafts
from app.capital.autonomy_policy import (
    CapitalOperatingPolicy,
    CapitalPolicyError,
)
from app.capital.validation_plan import research_snapshot


class CapitalValidationDraftTests(unittest.TestCase):
    def setUp(self):
        self.research = research_fixtures.CapitalResearchDraftTests()
        self.addCleanup(self.research.doCleanups)
        self.research.setUp()

        self.registration = (
            registry_fixtures.ValidationRegistrationRetryTests()
        )
        self.addCleanup(self.registration.doCleanups)
        self.registration.setUp()

        self.plan = deepcopy(self.registration.draft)
        self.plan["research"] = research_snapshot(self.research.parent)

        mocked = patch.object(
            drafts,
            "read_operating_policy",
            return_value=CapitalOperatingPolicy(),
        )
        self.policy = mocked.start()
        self.addCleanup(mocked.stop)

        def build(**arguments):
            result = deepcopy(self.plan)
            start = datetime.fromisoformat(arguments["start"])
            result["start"] = start.isoformat()
            result["end_exclusive"] = (
                start + timedelta(days=6)
            ).isoformat()
            return result

        mocked = patch.object(
            drafts, "build_validation_draft", side_effect=build
        )
        self.builder = mocked.start()
        self.addCleanup(mocked.stop)

    def prepare(self, **overrides):
        arguments = {
            "task_id": "validation-task",
            "research_id": self.research.parent.research_id,
        }
        arguments.update(overrides)
        return drafts.prepare_validation_draft(**arguments)

    def test_retry_reuses_exact_saved_window(self):
        first = self.prepare()
        second = self.prepare()
        self.assertEqual(first, second)
        self.builder.assert_called_once()

    def test_preparation_does_not_register_plan(self):
        self.prepare()
        with registry.locked_state() as state:
            self.assertEqual(state["plans"], {})

    def test_parent_candidate_is_unchanged(self):
        original = self.research.parent.to_dict()
        self.prepare()
        self.assertEqual(
            self.research.read()["candidates"][original["research_id"]],
            original,
        )

    def test_existing_reservation_moves_start_forward(self):
        end = (
            datetime.now(timezone.utc).replace(second=0, microsecond=0)
            + timedelta(days=2)
        )
        reserved = deepcopy(self.plan)
        reserved["start"] = (end - timedelta(hours=1)).isoformat()
        reserved["end_exclusive"] = end.isoformat()
        registry._register_plan(
            reserved,
            validate_binding=self.registration.binding,
        )

        saved = self.prepare()
        self.assertEqual(saved["draft"]["start"], end.isoformat())
        self.assertEqual(self.builder.call_count, 2)

    def test_request_cannot_switch_research_candidate(self):
        self.prepare()
        with self.assertRaises(ValueError):
            self.prepare(research_id="different-research")
        self.builder.assert_called_once()

    def test_changed_hypothesis_prevents_saving(self):
        self.plan["research"]["hypothesis"] = "Outdated hypothesis"
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertNotIn(
            "autonomy_validation_drafts", self.research.read()
        )

    def test_pause_prevents_preparation(self):
        self.policy.return_value = CapitalOperatingPolicy(paused=True)
        with self.assertRaises(CapitalPolicyError):
            self.prepare()
        self.builder.assert_not_called()

    def test_pause_after_build_prevents_saving(self):
        self.policy.side_effect = [
            CapitalOperatingPolicy(),
            CapitalOperatingPolicy(paused=True),
        ]
        with self.assertRaises(CapitalPolicyError):
            self.prepare()
        self.assertNotIn(
            "autonomy_validation_drafts", self.research.read()
        )

    def test_registration_uses_saved_draft_and_stable_key(self):
        saved = self.prepare()
        with patch.object(
            drafts,
            "register_validation_once",
            return_value={"plan_id": "validation_fixture"},
        ) as register:
            result = drafts.register_saved_validation(
                task_id="validation-task"
            )
        self.assertEqual(result["plan_id"], "validation_fixture")
        register.assert_called_once_with(
            saved["draft"],
            request_key="capital-validation:validation-task",
        )
        self.builder.assert_called_once()

    def test_registration_requires_saved_draft(self):
        with patch.object(drafts, "register_validation_once") as register:
            with self.assertRaises(KeyError):
                drafts.register_saved_validation(task_id="missing")
        register.assert_not_called()

    def test_registration_respects_disabled_controls(self):
        self.prepare()
        self.policy.return_value = CapitalOperatingPolicy(enabled=False)
        with patch.object(drafts, "register_validation_once") as register:
            with self.assertRaises(CapitalPolicyError):
                drafts.register_saved_validation(task_id="validation-task")
        register.assert_not_called()


if __name__ == "__main__":
    unittest.main()
