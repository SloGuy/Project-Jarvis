"""Check provider evidence against retained registration references."""

from copy import deepcopy
import unittest
from unittest.mock import patch

import test_validation_provider_verification as provider_fixture

from app.capital.run_validation import verify_registered_evidence


class ProviderAssessmentBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = provider_fixture.ProviderVerificationTests(
            methodName="test_matching_provider_report_is_verified"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

        self.report = self.fixture.report()
        self.packet = self.fixture.packet
        self.plan = self.packet["envelope"]["plan"]
        self.registration = self.report["validation_registration"]

        self.row = {
            "plan_id": self.registration["plan_id"],
            "status": "running",
            "registered_sha256": self.registration["sha256"],
            "envelope": deepcopy(self.packet["envelope"]),
            "provider_collection": deepcopy(
                self.packet["collection"]
            ),
        }

        self.patch = patch(
            "app.capital.run_validation.materialize_provider_evidence",
            return_value={
                "collection": deepcopy(self.packet["collection"]),
                "receipts": deepcopy(self.packet["receipts"]),
            },
        )
        self.materialize = self.patch.start()
        self.addCleanup(self.patch.stop)

    def verify(self):
        return verify_registered_evidence(
            self.plan,
            self.report,
            self.row,
            self.registration,
        )

    def test_matching_registered_packet_is_accepted(self):
        self.verify()
        self.materialize.assert_called_once_with(self.row)

    def test_missing_packet_is_rejected(self):
        self.report.pop("provider_evidence")

        with self.assertRaises(ValueError):
            self.verify()

    def test_changed_embedded_receipts_are_rejected(self):
        self.report["provider_evidence"]["receipts"] = []

        with self.assertRaises(ValueError):
            self.verify()

    def test_changed_retained_collection_is_rejected(self):
        self.row["provider_collection"]["sha256"] = "0" * 64

        with self.assertRaises(ValueError):
            self.verify()

    def test_unclaimed_registration_is_rejected(self):
        self.row["status"] = "registered"

        with self.assertRaises(ValueError):
            self.verify()

        self.materialize.assert_not_called()

    def test_wrong_plan_id_is_rejected(self):
        self.row["plan_id"] = "validation_another"

        with self.assertRaises(ValueError):
            self.verify()

    def test_changed_assessment_plan_is_rejected(self):
        self.plan = deepcopy(self.plan)
        self.plan["criteria"]["minimum_completed_trades"] = 1

        with self.assertRaises(ValueError):
            self.verify()

    def test_changed_snapshot_is_rejected(self):
        self.report["windows"][0]["snapshot"]["reason"] = "changed"

        with self.assertRaises(ValueError):
            self.verify()

    def test_mixed_report_evidence_is_rejected(self):
        self.report["witness_evidence"] = {}

        with self.assertRaises(ValueError):
            self.verify()

    def test_failed_collection_materialization_is_propagated(self):
        self.materialize.side_effect = ValueError(
            "Retained checkpoint mismatch."
        )

        with self.assertRaises(ValueError):
            self.verify()


if __name__ == "__main__":
    unittest.main()
