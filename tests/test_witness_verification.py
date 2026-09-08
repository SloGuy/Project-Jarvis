import copy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import unittest

from sqlalchemy.engine import Engine
from unittest.mock import patch

from app.capital.observation_witness import make_receipt
from app.capital.witnessed_history import WitnessedHistory
from app.capital.offline_verification import normalized, verify_report
from app.capital.position_simulation import PositionSimulation
from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY as POLICY

START = datetime(2026, 9, 4, 14, tzinfo=timezone.utc)


def fixture():
    rows = [
        {
            "id": index + 1,
            "asset_id": 1,
            "provider": "Finnhub",
            "price_usd": str(100 + index % 2),
            "observed_at": (
                START - timedelta(minutes=index + 1)
            ).isoformat(),
        }
        for index in range(48)
    ]
    receipt = make_receipt(
        rows, START - timedelta(seconds=1)
    )
    history = WitnessedHistory([receipt])
    simulation = PositionSimulation(
        symbol="SPY", policy=POLICY, fee_bps=5, slippage_bps=5
    )
    windows = []
    for index in range(3):
        at = START + timedelta(minutes=index)
        window = history.window(
            asset_id=1, provider="Finnhub",
            symbol="SPY", decision_at=at,
        )
        snapshot = window["snapshot"]
        simulation.step(
            snapshot, decision_at=at, risk_only=index % 5 != 0
        )
        values = asdict(snapshot)
        values["observation_at"] = snapshot.observation_at.isoformat()
        windows.append({
            "decision_at": at.isoformat(),
            "risk_only": index % 5 != 0,
            "observation_ids": window["observation_ids"],
            "snapshot": values,
        })
    return normalized({
        "mode": "single_asset_engineering_position_replay",
        "asset_id": 1, "symbol": "SPY", "provider": "Finnhub",
        "start": START.isoformat(),
        "end_exclusive": (START + timedelta(minutes=3)).isoformat(),
        "availability_verified": True,
        "witness_evidence": {
            "schema_version": 1, "receipts": [receipt],
        },
        "policy": asdict(POLICY),
        "windows": windows,
        "scenarios": {
            "specified_costs": {
                "fee_bps": simulation.ledger.fee_bps,
                "slippage_bps": simulation.ledger.slippage_bps,
                "account": simulation.ledger.mark(snapshot.latest_price_usd),
                "recovery_target": simulation.target,
                "opened_at": (
                    simulation.opened_at.isoformat()
                    if simulation.opened_at else None
                ),
                "events": simulation.events,
            },
        },
    })


class WitnessVerificationTests(unittest.TestCase):
    def test_complete_verification_without_database(self):
        report = fixture()
        before = copy.deepcopy(report)
        with patch.object(
            Engine, "connect", side_effect=AssertionError("Database used")
        ):
            self.assertEqual(verify_report(report)["status"], "matched")
        self.assertEqual(report, before)

    def test_flag_alone_is_rejected(self):
        report = fixture()
        del report["witness_evidence"]
        with self.assertRaisesRegex(ValueError, "requires witness"):
            verify_report(report)

    def test_changed_snapshot_is_rejected(self):
        report = fixture()
        report["windows"][0]["snapshot"]["mean_price_usd"] = "999"
        with self.assertRaisesRegex(ValueError, "Witness snapshot"):
            verify_report(report)

    def test_changed_ids_are_rejected(self):
        report = fixture()
        report["windows"][0]["observation_ids"] = [999]
        with self.assertRaisesRegex(ValueError, "observation IDs"):
            verify_report(report)

    def test_later_witness_cannot_support_earlier_decision(self):
        report = fixture()
        evidence = report["witness_evidence"]
        rows = evidence["receipts"][0]["payload"]["observations"]
        evidence["receipts"] = [
            make_receipt(rows, START + timedelta(minutes=1))
        ]
        with self.assertRaisesRegex(ValueError, "Witness snapshot"):
            verify_report(report)

    def test_tampered_receipt_is_rejected(self):
        report = fixture()
        receipt = report["witness_evidence"]["receipts"][0]
        receipt["payload"]["observations"][0]["price_usd"] = "1"
        with self.assertRaises(ValueError):
            verify_report(report)

    def test_legacy_unverified_report_still_works(self):
        report = fixture()
        report.pop("witness_evidence")
        report["availability_verified"] = False
        self.assertEqual(verify_report(report)["status"], "matched")


if __name__ == "__main__":
    unittest.main()
