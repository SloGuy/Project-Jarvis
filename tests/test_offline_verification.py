import copy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import unittest

from app.capital.offline_verification import normalized, verify_report
from app.capital.position_simulation import PositionSimulation
from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY as POLICY
from app.capital.mean_reversion_math import MeanReversionSnapshot
from decimal import Decimal as D


def fixture():
    start = datetime(2026, 9, 4, 14, tzinfo=timezone.utc)
    simulation = PositionSimulation(
        symbol="SPY", policy=POLICY, fee_bps=5, slippage_bps=5
    )
    windows = []
    for index in range(16):
        at = start + timedelta(minutes=index)
        snapshot = MeanReversionSnapshot(
            symbol="SPY", observation_at=at - timedelta(seconds=1),
            latest_price_usd=D("100" if index == 15 else "98"),
            mean_price_usd=D("100"), standard_deviation_usd=D("1"),
            z_score=D("-2"), observation_count=48,
            usable=True, reason=None,
        )
        simulation.step(snapshot, decision_at=at, risk_only=index % 5 != 0)
        values = asdict(snapshot)
        values["observation_at"] = snapshot.observation_at.isoformat()
        windows.append({
            "decision_at": at.isoformat(),
            "risk_only": index % 5 != 0,
            "snapshot": values,
        })
    return normalized({
        "mode": "single_asset_engineering_position_replay",
        "start": start.isoformat(),
        "end_exclusive": (start + timedelta(minutes=16)).isoformat(),
        "policy": asdict(POLICY), "windows": windows,
        "scenarios": {"costs": {
            "fee_bps": simulation.ledger.fee_bps,
            "slippage_bps": simulation.ledger.slippage_bps,
            "account": simulation.ledger.mark(D("100")),
            "recovery_target": simulation.target,
            "opened_at": None,
            "events": simulation.events,
        }},
    })


class VerificationTests(unittest.TestCase):
    def test_complete_match_without_mutating_input(self):
        report = fixture()
        before = copy.deepcopy(report)
        result = verify_report(report)
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["scenarios"]["costs"]["fills"], 2)
        self.assertEqual(report, before)

    def test_changed_event_is_rejected(self):
        report = fixture()
        report["scenarios"]["costs"]["events"][0]["action"] = "sell"
        with self.assertRaisesRegex(ValueError, "Replay mismatch"):
            verify_report(report)

    def test_changed_balance_is_rejected(self):
        report = fixture()
        report["scenarios"]["costs"]["account"]["cash"] = "999999"
        with self.assertRaisesRegex(ValueError, "Replay mismatch"):
            verify_report(report)

    def test_missing_tick_is_rejected(self):
        report = fixture()
        report["windows"].pop(4)
        with self.assertRaises(ValueError):
            verify_report(report)

    def test_changed_schedule_is_rejected(self):
        report = fixture()
        report["windows"][1]["risk_only"] = False
        with self.assertRaisesRegex(ValueError, "Schedule mismatch"):
            verify_report(report)


if __name__ == "__main__":
    unittest.main()
