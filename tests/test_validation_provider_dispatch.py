"""Test the existing offline verifier's provider-evidence route."""

import unittest

import test_validation_provider_verification as provider_fixture

from app.capital.witness_verification import verify_witness_inputs


class ProviderVerificationDispatchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = provider_fixture.ProviderVerificationTests(
            methodName="test_matching_provider_report_is_verified"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_provider_report_uses_input_reconstruction(self):
        self.assertTrue(
            verify_witness_inputs(self.fixture.report())
        )

    def test_provider_route_rejects_changed_snapshot(self):
        report = self.fixture.report()
        report["windows"][0]["snapshot"]["reason"] = "changed"

        with self.assertRaises(ValueError):
            verify_witness_inputs(report)

    def test_provider_route_rejects_changed_input_ids(self):
        report = self.fixture.report()
        report["windows"][0]["observation_ids"] = []

        with self.assertRaises(ValueError):
            verify_witness_inputs(report)

    def test_provider_route_rejects_mixed_evidence(self):
        report = self.fixture.report()
        report["witness_collection"] = {}

        with self.assertRaises(ValueError):
            verify_witness_inputs(report)

    def test_provider_failure_does_not_use_legacy_route(self):
        report = self.fixture.report()
        report["provider_evidence"]["receipts"] = []

        with self.assertRaises(ValueError):
            verify_witness_inputs(report)

    def test_legacy_report_without_evidence_remains_unverified(self):
        self.assertFalse(
            verify_witness_inputs({
                "availability_verified": False,
            })
        )

    def test_legacy_availability_claim_still_requires_evidence(self):
        with self.assertRaises(ValueError):
            verify_witness_inputs({
                "availability_verified": True,
            })


if __name__ == "__main__":
    unittest.main()
