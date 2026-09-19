"""Factory review-packet tests; no production database access."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.capital import experiment_factory_packet as packet


class FactoryPacketTests(unittest.TestCase):
    def setUp(self):
        self.record = SimpleNamespace(
            request_key="agent:request-1",
            research_id="research_test",
            requested_by="research-agent",
            status="awaiting_review",
        )
        self.review = {
            "eligible_for_operator_review": True,
            "blockers": [],
            "research": {"research_id": "research_test"},
            "verified_plan_bindings": [{
                "plan_id": "plan-1",
                "plan_sha256": "a" * 64,
            }],
            "proposed_experiment": {
                "status": "planned",
                "execution_mode": "disabled",
                "portfolio_type": "paper",
                "portfolio_active": False,
                "starting_capital_usd": "1000.00",
            },
            "human_approval_required": True,
            "creation_authorized": False,
            "execution_authorized": False,
            "live_capital_authorized": False,
        }
        self.session = MagicMock()
        self.session.get.return_value = self.record

        handle = patch.object(packet, "SessionLocal")
        factory = handle.start()
        self.addCleanup(handle.stop)
        factory.return_value.__enter__.return_value = self.session

        handle = patch.object(packet, "build_factory_review")
        self.build_review = handle.start()
        self.addCleanup(handle.stop)
        self.build_review.return_value = self.review

    def prepare(self):
        return packet.prepare_factory_packet("agent:request-1")

    def test_packet_is_deterministic_and_read_only(self):
        before = deepcopy(self.review)
        first = self.prepare()
        second = self.prepare()
        self.assertEqual(first, second)
        self.assertEqual(
            first["sha256"], packet.packet_digest(first["payload"])
        )
        self.assertEqual(
            first["payload"]["action"], "create_inactive_paper_experiment"
        )
        self.assertEqual(self.review, before)
        self.session.add.assert_not_called()
        self.session.flush.assert_not_called()
        self.session.commit.assert_not_called()

    def test_evidence_change_changes_digest(self):
        first = self.prepare()["sha256"]
        self.review["verified_plan_bindings"][0]["plan_sha256"] = "b" * 64
        self.assertNotEqual(first, self.prepare()["sha256"])

    def test_configuration_change_changes_digest(self):
        first = self.prepare()["sha256"]
        self.review["proposed_experiment"]["starting_capital_usd"] = "500.00"
        self.assertNotEqual(first, self.prepare()["sha256"])

    def test_request_change_changes_digest(self):
        first = self.prepare()["sha256"]
        self.record.requested_by = "another-agent"
        self.assertNotEqual(first, self.prepare()["sha256"])

    def test_missing_request_is_rejected(self):
        self.session.get.return_value = None
        with self.assertRaises(KeyError):
            self.prepare()
        self.build_review.assert_not_called()

    def test_nonpending_request_is_rejected(self):
        for status in ("created", "rejected"):
            with self.subTest(status=status):
                self.record.status = status
                with self.assertRaisesRegex(ValueError, "awaiting review"):
                    self.prepare()
        self.build_review.assert_not_called()

    def test_blocked_review_is_rejected(self):
        self.review["eligible_for_operator_review"] = False
        self.review["blockers"] = ["Insufficient evidence"]
        with self.assertRaisesRegex(ValueError, "Insufficient evidence"):
            self.prepare()

    def test_wrong_candidate_is_rejected(self):
        self.review["research"]["research_id"] = "research_other"
        with self.assertRaisesRegex(ValueError, "different candidate"):
            self.prepare()

    def test_authority_flags_must_explicitly_deny_authority(self):
        for field in (
            "creation_authorized",
            "execution_authorized",
            "live_capital_authorized",
        ):
            for value in (True, None, 0):
                with self.subTest(field=field, value=value):
                    self.review[field] = value
                    with self.assertRaisesRegex(ValueError, "authority"):
                        self.prepare()
                    self.review[field] = False

    def test_human_approval_cannot_be_removed(self):
        self.review["human_approval_required"] = False
        with self.assertRaisesRegex(ValueError, "Human approval"):
            self.prepare()

    def test_nonfinite_values_cannot_be_hashed(self):
        with self.assertRaises(ValueError):
            packet.packet_digest({"invalid": float("nan")})


if __name__ == "__main__":
    unittest.main()
