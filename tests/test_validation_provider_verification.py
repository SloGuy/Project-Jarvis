"""Verify embedded provider evidence using real receipt fixtures."""

from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
import json
import unittest

import test_validation_quote_receipt as receipt_fixture

from app.capital.observation_witness import digest
from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY
from app.capital.validation_input_contract import make_input_contract
from app.capital.validation_plan import seal_plan
from app.capital.validation_provider_verification import (
    open_provider_packet,
    verify_provider_inputs,
)


def normalized(value):
    return json.loads(
        json.dumps(value, default=str, allow_nan=False)
    )


def refresh(value):
    value["sha256"] = digest({
        key: item
        for key, item in value.items()
        if key != "sha256"
    })


class ProviderVerificationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = receipt_fixture.ValidationQuoteReceiptTests(
            methodName="test_valid_receipt"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

        fixture = self.fixture
        draft = deepcopy(fixture.envelope["plan"])
        draft.pop("created_at")
        draft["schema_version"] = 2
        draft["policy"] = normalized(
            asdict(MEAN_REVERSION_V2_1000_POLICY)
        )
        draft["input_contract"] = make_input_contract(
            policy=draft["policy"]
        )

        fixture.envelope = seal_plan(
            draft, now=fixture.created
        )
        fixture.sha = fixture.envelope["sha256"]

        receipts = [fixture.make()]
        head = digest({
            "index": 1,
            "previous": None,
            "receipt": receipts[0],
        })

        checkpoint = {
            "schema_version": 1,
            "binding": {
                "receipt_type": "provider_time_v1",
                "plan_sha256": fixture.sha,
                "bound_at": fixture.bound.isoformat(),
            },
            "count": 1,
            "head": head,
            "sealed": True,
        }
        refresh(checkpoint)

        collection = {
            "schema_version": 1,
            "kind": "provider_time_v1",
            "plan_sha256": fixture.sha,
            "bound_at": fixture.bound.isoformat(),
            "status": "sealed",
            "store_checkpoint": checkpoint,
        }
        refresh(collection)

        self.packet = {
            "schema_version": 1,
            "envelope": fixture.envelope,
            "registered_sha256": fixture.sha,
            "collection": collection,
            "receipts": receipts,
        }

    def open(self, packet=None, **arguments):
        return open_provider_packet(
            packet if packet is not None else self.packet,
            expected_sha256=self.fixture.sha,
            **arguments,
        )

    def report(self):
        decision = (
            self.fixture.start + timedelta(minutes=1)
        ).isoformat()

        with self.open() as history:
            window = history.window(decision_at=decision)

        plan = self.fixture.envelope["plan"]

        return {
            "designation": "prospective_validation",
            "validation_registration": {
                "plan_id": "validation_isolated_test",
                "sha256": self.fixture.sha,
            },
            **{
                field: deepcopy(plan[field])
                for field in (
                    "asset_id",
                    "symbol",
                    "provider",
                    "start",
                    "end_exclusive",
                    "policy",
                    "execution_manifest",
                )
            },
            "provider_evidence": deepcopy(self.packet),
            "availability_verified": True,
            "windows": [{
                "decision_at": decision,
                "snapshot": normalized(
                    asdict(window["snapshot"])
                ),
                "observation_ids": window["observation_ids"],
                "provider_input": window["provider_input"],
            }],
            "scenarios": {
                "zero_cost": {
                    "fee_bps": "0",
                    "slippage_bps": "0",
                },
                "specified_costs": {
                    "fee_bps": plan["fee_bps"],
                    "slippage_bps": plan["slippage_bps"],
                },
            },
        }

    def test_packet_reconstructs_without_original_quote_file(self):
        for path in self.fixture.directory.glob("*.json"):
            path.unlink()

        with self.open() as history:
            result = history.window(
                decision_at=(
                    self.fixture.start + timedelta(minutes=1)
                ).isoformat()
            )

        self.assertEqual(
            result["observation_ids"], [self.fixture.ident]
        )

    def test_matching_provider_report_is_verified(self):
        self.assertTrue(
            verify_provider_inputs(self.report())
        )

    def test_snapshot_change_is_rejected(self):
        report = self.report()
        report["windows"][0]["snapshot"]["reason"] = "altered"

        with self.assertRaises(ValueError):
            verify_provider_inputs(report)

    def test_input_evidence_change_is_rejected(self):
        report = self.report()
        report["windows"][0]["provider_input"][
            "visible_receipt_count"
        ] = 99

        with self.assertRaises(ValueError):
            verify_provider_inputs(report)

    def test_rehashed_chain_head_change_is_rejected(self):
        packet = deepcopy(self.packet)
        collection = packet["collection"]
        checkpoint = collection["store_checkpoint"]
        checkpoint["head"] = "0" * 64
        refresh(checkpoint)
        refresh(collection)

        with self.assertRaises(ValueError):
            with self.open(packet):
                pass

    def test_receipt_change_is_rejected(self):
        packet = deepcopy(self.packet)
        packet["receipts"][0]["sha256"] = "0" * 64

        with self.assertRaises(ValueError):
            with self.open(packet):
                pass

    def test_missing_receipt_is_rejected(self):
        packet = deepcopy(self.packet)
        packet["receipts"] = []

        with self.assertRaises(ValueError):
            with self.open(packet):
                pass

    def test_unsealed_checkpoint_is_rejected(self):
        packet = deepcopy(self.packet)
        collection = packet["collection"]
        collection["store_checkpoint"]["sealed"] = False
        refresh(collection["store_checkpoint"])
        refresh(collection)

        with self.assertRaises(ValueError):
            with self.open(packet):
                pass

    def test_retained_collection_mismatch_is_rejected(self):
        retained = deepcopy(self.packet["collection"])
        retained["sha256"] = "0" * 64

        with self.assertRaises(ValueError):
            with self.open(expected_collection=retained):
                pass

    def test_changed_registered_costs_are_rejected(self):
        report = self.report()
        report["scenarios"]["specified_costs"]["fee_bps"] = "0"

        with self.assertRaises(ValueError):
            verify_provider_inputs(report)

    def test_mixed_evidence_is_rejected(self):
        report = self.report()
        report["witness_evidence"] = {}

        with self.assertRaises(ValueError):
            verify_provider_inputs(report)

    def test_false_availability_claim_is_rejected(self):
        report = self.report()
        report["availability_verified"] = False

        with self.assertRaises(ValueError):
            verify_provider_inputs(report)


if __name__ == "__main__":
    unittest.main()
