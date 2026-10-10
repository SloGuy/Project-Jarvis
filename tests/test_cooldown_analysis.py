"""Marked-metric tests and real receipt-backed analysis integration."""

import json
import unittest
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

import test_cooldown_provider_integration as integration_fixture

from app.capital.cooldown_analysis import (
    _metrics, analyze_cooldown_packet,
)
from app.capital.cooldown_comparison import compare_loss_cooldown
from app.capital.replay_analysis import analyze_report
from app.capital import validation_registry as registry
from test_position_simulation import NOW, POLICY, snapshot


class CooldownMetricTests(unittest.TestCase):
    def make_report(self):
        inputs = []
        windows = []
        for minute in range(30):
            at = NOW + timedelta(minutes=minute)
            value = snapshot(at, "90" if minute == 11 else "98")
            risk_only = minute % 5 != 0
            inputs.append({
                "snapshot": value,
                "decision_at": at,
                "risk_only": risk_only,
            })
            values = asdict(value)
            values["observation_at"] = value.observation_at.isoformat()
            windows.append({
                "snapshot": values,
                "decision_at": at.isoformat(),
                "risk_only": risk_only,
            })
        report = compare_loss_cooldown(
            symbol="SPY", policy=POLICY,
            fee_bps=5, slippage_bps=5,
            decision_inputs=inputs,
        )
        report["windows"] = windows
        return report

    def test_marked_equity_includes_remaining_open_position(self):
        report = self.make_report()
        baseline = _metrics(report, "baseline", POLICY)
        account = report["baseline"]
        expected = D(str(account["cash_usd"])) + (
            D(str(account["open_quantity"])) * D("98")
        ).quantize(D("0.00000001"))

        self.assertEqual(D(baseline["ending_equity"]), expected)
        self.assertGreater(account["open_quantity"], 0)
        self.assertEqual(baseline["completed_trades"], 1)
        self.assertNotEqual(
            expected,
            POLICY.starting_capital_usd + account["net_realized_usd"],
        )

    def test_drawdown_and_quality_are_measured_for_both_accounts(self):
        report = self.make_report()
        for name in ("baseline", "intervention"):
            summary = _metrics(report, name, POLICY)
            self.assertGreater(
                D(summary["maximum_marked_drawdown_percent"]), 0
            )
            self.assertEqual(summary["data_quality"]["regular_ticks"], 6)
            self.assertEqual(
                summary["data_quality"].get(
                    "missing_or_stale_reference_ticks", 0
                ), 0,
            )
            self.assertEqual(len(summary["equity_curve"]), 30)

    def test_benchmark_uses_identical_costs_and_inputs(self):
        report = self.make_report()
        baseline = _metrics(report, "baseline", POLICY)
        intervention = _metrics(report, "intervention", POLICY)

        for field in (
            "entry_at", "ending_equity", "return_percent",
            "maximum_marked_drawdown_percent", "fee_bps", "slippage_bps",
        ):
            self.assertEqual(
                baseline["benchmark"][field],
                intervention["benchmark"][field],
            )
        self.assertFalse(baseline["benchmark"]["forced_liquidation"])
        self.assertLess(
            D(baseline["benchmark"]["ending_equity"]),
            POLICY.starting_capital_usd,
        )

    def test_missing_last_price_uses_flagged_carry_forward_mark(self):
        report = self.make_report()
        window = report["windows"][-1]
        window["snapshot"]["latest_price_usd"] = None
        window["snapshot"]["observation_at"] = None
        window["snapshot"]["usable"] = False

        summary = _metrics(report, "baseline", POLICY)
        self.assertEqual(
            summary["data_quality"]["missing_or_stale_reference_ticks"], 1
        )
        self.assertFalse(summary["equity_curve"][-1]["fresh_reference"])
        self.assertEqual(
            summary["equity_curve"][-1]["equity"],
            summary["equity_curve"][-2]["equity"],
        )

    def test_no_prices_leave_drawdown_and_benchmark_entry_unknown(self):
        at = NOW
        value = replace(
            snapshot(at),
            observation_at=None,
            latest_price_usd=None,
            usable=False,
        )
        report = compare_loss_cooldown(
            symbol="SPY", policy=POLICY, fee_bps=5, slippage_bps=5,
            decision_inputs=[{
                "snapshot": value, "decision_at": at, "risk_only": False,
            }],
        )
        values = asdict(value)
        report["windows"] = [{
            "snapshot": values,
            "decision_at": at.isoformat(),
            "risk_only": False,
        }]
        summary = _metrics(report, "baseline", POLICY)

        self.assertFalse(summary["has_equity_marks"])
        self.assertIsNone(summary["maximum_marked_drawdown_percent"])
        self.assertIsNone(summary["benchmark"]["entry_at"])
        self.assertEqual(summary["data_quality"]["unmarked_ticks"], 1)

    def test_accounting_mismatch_is_rejected(self):
        report = self.make_report()
        report["baseline"]["cash_usd"] += D("1")
        with self.assertRaisesRegex(ValueError, "accounting mismatch"):
            _metrics(report, "baseline", POLICY)


class CooldownAnalysisIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = integration_fixture.CooldownProviderIntegrationTests(
            methodName="test_real_collection_supplies_both_accounts_without_database"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare_completed()
        self.report = self.fixture.compare()
        self.row = registry.get_plan(self.fixture.fixture.plan_id)

    def analyze(self, report=None):
        return analyze_cooldown_packet(
            self.report if report is None else report,
            policy=POLICY,
            expected_manifest=self.fixture.manifest,
            expected_plan_id=self.row["plan_id"],
            expected_sha256=self.row["registered_sha256"],
            expected_collection=self.row["provider_collection"],
        )

    def test_baseline_metrics_match_existing_verified_analysis(self):
        directory = Path(self.row["history"][-1]["detail"]).parent
        original = json.loads((directory / "report.json").read_text())
        reference = analyze_report(original)["scenarios"]["specified_costs"]
        result = self.analyze()

        for name in ("baseline", "intervention"):
            summary = result["accounts"][name]
            for field in (
                "ending_equity", "return_percent",
                "maximum_marked_drawdown_percent",
                "completed_trades", "winning_trades", "losing_trades",
                "realized_pnl", "data_quality", "equity_curve",
            ):
                self.assertEqual(summary[field], reference[field])

        self.assertTrue(result["input_availability_verified"])
        self.assertFalse(result["promotion_authorized"])
        self.fixture.fixture.database.assert_not_called()

    def test_analysis_rejects_changed_packet_before_metrics(self):
        changed = deepcopy(self.report)
        changed["baseline"]["cash_usd"] += D("1")
        with self.assertRaises(ValueError):
            self.analyze(changed)


if __name__ == "__main__":
    unittest.main()
