"""Version-2 input contracts and retained plan bindings."""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_validation_quote_receipt as receipt_fixture

from app.capital.validation_input_contract import (
    SOURCE_FILES,
    make_input_contract,
    validate_input_contract,
)
from app.capital.validation_plan import (
    check_binding,
    plan_digest,
    validate_plan,
    verify_plan,
)


class ValidationInputContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = (
            receipt_fixture.ValidationQuoteReceiptTests(
                "test_valid_receipt"
            )
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

        self.policy = {
            "max_price_age_seconds": 120,
        }

        # Structural fixture only; never registered.
        self.contract = {
            "schema_version": 1,
            "kind": "provider_time_v1",
            "maximum_provider_age_seconds": 120,
            "maximum_capture_age_seconds": 120,
            "capture_interval_seconds": 60,
            "source_sha256": {
                name: "0" * 64
                for name in SOURCE_FILES
            },
        }

        self.plan = deepcopy(self.fixture.envelope["plan"])
        self.plan.update(
            schema_version=2,
            policy=deepcopy(self.policy),
            input_contract=deepcopy(self.contract),
        )

    def validate_contract(self, contract):
        return validate_input_contract(
            contract,
            policy=self.policy,
            verify_sources=False,
        )

    def binding(self, plan):
        candidate = SimpleNamespace(
            to_dict=lambda: deepcopy(plan["research"])
        )
        return check_binding(
            plan,
            candidate,
            plan["strategy_version"],
            deepcopy(plan["policy"]),
            deepcopy(plan["execution_manifest"]),
        )

    def test_version_one_remains_valid(self):
        plan = deepcopy(self.fixture.envelope["plan"])
        self.assertIs(validate_plan(plan), plan)

    def test_version_two_contract_is_required(self):
        plan = deepcopy(self.plan)
        del plan["input_contract"]

        with self.assertRaises(ValueError):
            validate_plan(plan)

    def test_version_one_cannot_silently_gain_contract(self):
        plan = deepcopy(self.plan)
        plan["schema_version"] = 1

        with self.assertRaises(ValueError):
            validate_plan(plan)

    def test_version_two_structural_contract_is_valid(self):
        self.assertIs(validate_plan(self.plan), self.plan)

    def test_weakened_provider_age_is_rejected(self):
        contract = deepcopy(self.contract)
        contract["maximum_provider_age_seconds"] = 300

        with self.assertRaisesRegex(ValueError, "risk policy"):
            self.validate_contract(contract)

    def test_capture_age_cannot_exceed_provider_age(self):
        contract = deepcopy(self.contract)
        contract["maximum_capture_age_seconds"] = 121

        with self.assertRaisesRegex(ValueError, "Capture-age"):
            self.validate_contract(contract)

    def test_capture_interval_requires_margin(self):
        contract = deepcopy(self.contract)
        contract["capture_interval_seconds"] = 61

        with self.assertRaisesRegex(ValueError, "margin"):
            self.validate_contract(contract)

    def test_missing_source_fingerprint_is_rejected(self):
        contract = deepcopy(self.contract)
        del contract["source_sha256"][SOURCE_FILES[0]]

        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.validate_contract(contract)

    def test_invalid_source_fingerprint_is_rejected(self):
        contract = deepcopy(self.contract)
        contract["source_sha256"][SOURCE_FILES[0]] = "invalid"

        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.validate_contract(contract)

    def test_boolean_age_limit_is_rejected(self):
        contract = deepcopy(self.contract)
        contract["maximum_provider_age_seconds"] = True

        with self.assertRaises(ValueError):
            self.validate_contract(contract)

    def test_real_source_manifest_can_be_built(self):
        contract = make_input_contract(policy=self.policy)

        self.assertEqual(
            contract["maximum_provider_age_seconds"],
            120,
        )
        self.assertEqual(
            set(contract["source_sha256"]),
            set(SOURCE_FILES),
        )
        self.assertEqual(
            validate_input_contract(
                contract,
                policy=self.policy,
                verify_sources=True,
            ),
            contract,
        )

    def test_changed_sources_block_binding(self):
        changed = {
            name: "1" * 64
            for name in SOURCE_FILES
        }

        with patch(
            "app.capital.validation_input_contract."
            "capture_input_sources",
            return_value=changed,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "sources changed",
            ):
                self.binding(self.plan)

    def test_matching_sources_allow_binding(self):
        with patch(
            "app.capital.validation_input_contract."
            "capture_input_sources",
            return_value=deepcopy(
                self.contract["source_sha256"]
            ),
        ):
            self.binding(self.plan)

    def test_contract_change_breaks_retained_plan_hash(self):
        envelope = {
            "plan": deepcopy(self.plan),
            "sha256": plan_digest(self.plan),
        }
        retained = envelope["sha256"]

        # A valid but changed cadence still changes the plan identity.
        envelope["plan"]["input_contract"][
            "capture_interval_seconds"
        ] = 30
        envelope["sha256"] = plan_digest(envelope["plan"])

        with self.assertRaisesRegex(ValueError, "changed"):
            verify_plan(
                envelope,
                expected_sha256=retained,
            )

    def test_missing_freshness_setting_is_rejected(self):
        with self.assertRaises(ValueError):
            make_input_contract(policy={})

    def test_returned_contract_is_an_independent_copy(self):
        validated = self.validate_contract(self.contract)
        validated["source_sha256"][SOURCE_FILES[0]] = "1" * 64

        self.assertEqual(
            self.contract["source_sha256"][SOURCE_FILES[0]],
            "0" * 64,
        )


if __name__ == "__main__":
    unittest.main()
