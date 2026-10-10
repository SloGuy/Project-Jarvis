"""Atomic cooldown registration with temporary registry and research state."""

import copy
import unittest
from unittest.mock import patch

import test_cooldown_contract as fixtures

from app.capital import cooldown_registration as registration
from app.capital import research_store
from app.capital import validation_registry as registry
from app.capital import autonomy_trade_research as queue
from app.capital.autonomy_validation_registration import PREFIX as LEGACY_PREFIX
from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY as POLICY


class CooldownRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CooldownContractTests(
            methodName="test_real_completed_request_and_plan_produce_contract"
        )
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

        self.request = copy.deepcopy(self.fixture.request)
        self.request_id = self.request["request_id"]
        self.draft = copy.deepcopy(self.fixture.row["envelope"]["plan"])
        self.draft.pop("created_at")
        self.draft["created_by"] = "capital.cooldown"

        from datetime import datetime

        for field in ("start", "end_exclusive"):
            self.draft[field] = datetime.fromisoformat(
                self.draft[field]
            ).replace(second=0, microsecond=0).isoformat()

        # Retain the same completed request used by the contract fixture.
        with research_store.locked_research_state(write=True) as state:
            state[queue.STORE_KEY][self.request_id] = copy.deepcopy(
                self.request
            )
        self.research_before = (
            research_store.RESEARCH_STATE_FILE.read_bytes()
        )

        self.authority = self.mock(
            registration, "_authorize", return_value=None
        )
        self.binding = self.mock(
            registration, "_current_binding", return_value=None
        )
        self.mock(
            registry, "now_utc", return_value=self.fixture.now
        )
        self.reader = self.mock(
            __import__(
                "app.capital.cooldown_provider_comparison",
                fromlist=["authorize_read"],
            ),
            "authorize_read",
            return_value=None,
        )

    def mock(self, target, name, **keywords):
        pending = patch.object(target, name, **keywords)
        result = pending.start()
        self.addCleanup(pending.stop)
        return result

    def register(self, draft=None):
        return registration.register_cooldown_once(
            self.draft if draft is None else draft,
            request_id=self.request_id,
            policy=POLICY,
        )

    def read(self, plan_id):
        return registration.read_registered_cooldown(
            plan_id, policy=POLICY
        )

    def registry_bytes(self):
        path = registry.DIRECTORY / "registry.json"
        return path.read_bytes() if path.exists() else None

    def plans(self):
        with registry.locked_state() as state:
            return copy.deepcopy(state["plans"])

    def test_plan_and_contract_are_saved_together(self):
        row = self.register()
        retained = registry.get_plan(row["plan_id"])
        self.assertEqual(retained, row)
        self.assertEqual(len(self.plans()), 1)
        self.assertEqual(
            row["cooldown_contract_sha256"],
            row["cooldown_contract"]["sha256"],
        )
        self.assertEqual(self.read(row["plan_id"]), row)

    def test_contract_failure_leaves_no_registration(self):
        before = self.registry_bytes()
        self.mock(
            registration,
            "build_cooldown_contract",
            side_effect=ValueError("contract rejected"),
        )
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual(self.registry_bytes(), before)
        self.assertEqual(self.plans(), {})

    def test_initial_denial_prevents_binding_and_persistence(self):
        self.authority.side_effect = PermissionError("paused")
        before = self.registry_bytes()
        with self.assertRaises(PermissionError):
            self.register()
        self.binding.assert_not_called()
        self.assertEqual(self.registry_bytes(), before)

    def test_final_denial_preserves_registry(self):
        self.authority.side_effect = [
            None, PermissionError("paused before commit"),
        ]
        before = self.registry_bytes()
        with self.assertRaises(PermissionError):
            self.register()
        self.assertEqual(self.registry_bytes(), before)
        self.assertEqual(self.plans(), {})

    def test_binding_failure_preserves_registry(self):
        self.binding.side_effect = ValueError("research changed")
        before = self.registry_bytes()
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual(self.registry_bytes(), before)

    def test_changed_configuration_blocks_registration(self):
        self.mock(
            queue, "current_configuration",
            return_value={"changed": True},
        )
        before = self.registry_bytes()
        with self.assertRaises(ValueError):
            self.register()
        self.binding.assert_not_called()
        self.assertEqual(self.registry_bytes(), before)

    def test_missing_retained_request_is_rejected(self):
        with research_store.locked_research_state(write=True) as state:
            del state[queue.STORE_KEY][self.request_id]
        before = self.registry_bytes()
        with self.assertRaises(KeyError):
            self.register()
        self.assertEqual(self.registry_bytes(), before)

    def test_retry_returns_original_registration(self):
        first = self.register()
        second = self.register()
        self.assertEqual(first, second)
        self.assertEqual(len(self.plans()), 1)

    def test_retry_after_period_preserves_lifecycle(self):
        from datetime import timedelta

        first = self.register()
        with registry.locked_state(write=True) as state:
            state["plans"][first["plan_id"]]["status"] = "failed"
        self.mock(
            registry, "now_utc",
            return_value=self.fixture.now + timedelta(days=20),
        )
        again = self.register()
        self.assertEqual(again["status"], "failed")
        self.assertEqual(
            again["cooldown_contract"], first["cooldown_contract"]
        )
        self.assertEqual(len(self.plans()), 1)

    def test_conflicting_retry_is_rejected(self):
        first = self.register()
        changed = copy.deepcopy(self.draft)
        changed["fee_bps"] = "6"
        before = self.registry_bytes()
        with self.assertRaises(ValueError):
            self.register(changed)
        self.assertEqual(self.registry_bytes(), before)
        self.assertEqual(self.read(first["plan_id"]), first)

    def test_existing_reservation_blocks_comparison(self):
        envelope = copy.deepcopy(self.fixture.row["envelope"])
        row = {
            "plan_id": "validation_reserved",
            "envelope": envelope,
            "registered_sha256": envelope["sha256"],
            "status": "failed",
            "history": [{
                "status": "registered",
                "at": envelope["plan"]["created_at"],
            }],
        }
        with registry.locked_state(write=True) as state:
            state["plans"][row["plan_id"]] = row
        before = self.registry_bytes()
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual(self.registry_bytes(), before)

    def test_corrupt_contract_is_not_overwritten_on_retry(self):
        row = self.register()
        with registry.locked_state(write=True) as state:
            saved = state["plans"][row["plan_id"]]["cooldown_contract"]
            saved["contract"]["scope"] = "changed"
        before = self.registry_bytes()
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual(self.registry_bytes(), before)
        with self.assertRaises(ValueError):
            self.read(row["plan_id"])

    def test_changed_retained_origin_blocks_read(self):
        row = self.register()
        with research_store.locked_research_state(write=True) as state:
            state[queue.STORE_KEY][self.request_id]["proposal"][
                "rationale"
            ] = "Changed after registration."
        with self.assertRaises(ValueError):
            self.read(row["plan_id"])

    def test_owner_is_separate_from_existing_validation_worker(self):
        row = self.register()
        owner = row["envelope"]["plan"]["created_by"]
        self.assertTrue(owner.startswith(registration.PREFIX))
        self.assertFalse(owner.startswith(LEGACY_PREFIX))

    def test_registration_does_not_change_research_or_call_model(self):
        self.register()
        self.assertEqual(
            research_store.RESEARCH_STATE_FILE.read_bytes(),
            self.research_before,
        )
        self.fixture.model.assert_called_once()

    def test_returned_registration_is_an_independent_copy(self):
        row = self.register()
        plan_id = row["plan_id"]
        row["cooldown_contract"]["contract"]["scope"] = "local change"
        retained = self.read(plan_id)
        self.assertNotEqual(
            retained["cooldown_contract"], row["cooldown_contract"]
        )

    def test_wrong_draft_owner_is_rejected(self):
        changed = copy.deepcopy(self.draft)
        changed["created_by"] = "capital.validation"
        with self.assertRaises(ValueError):
            self.register(changed)
        self.assertEqual(self.plans(), {})


if __name__ == "__main__":
    unittest.main()
