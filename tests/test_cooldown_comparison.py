"""Compare independent replay accounts using identical decision inputs."""

import unittest
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

from app.capital.cooldown_comparison import compare_loss_cooldown
from app.capital.position_simulation import PositionSimulation
from test_position_simulation import NOW, POLICY, snapshot


class CooldownComparisonTests(unittest.TestCase):
    def inputs(self, *, losing=True):
        rows = []
        for minute, price in (
            (0, "98"),
            (5, "98"),
            (10, "98"),
            (11, "90" if losing else "100"),
            (12, "98"),
            (13, "98"),
            (14, "98"),
        ):
            at = NOW + timedelta(minutes=minute)
            rows.append({
                "snapshot": snapshot(at, price),
                "decision_at": at,
                "risk_only": minute == 11,
            })
        return rows

    def compare(self, rows=None):
        return compare_loss_cooldown(
            symbol="SPY",
            policy=POLICY,
            fee_bps=5,
            slippage_bps=5,
            decision_inputs=self.inputs() if rows is None else rows,
        )

    def test_baseline_matches_original_engine(self):
        rows = self.inputs()
        baseline = PositionSimulation(
            symbol="SPY", policy=POLICY, fee_bps=5, slippage_bps=5
        )
        for row in rows:
            baseline.step(
                row["snapshot"],
                decision_at=row["decision_at"],
                risk_only=row["risk_only"],
            )

        result = self.compare(rows)["baseline"]
        self.assertEqual(result["events"], baseline.events)
        self.assertEqual(result["fills"], baseline.ledger.fills)
        self.assertEqual(result["cash_usd"], baseline.ledger.cash)
        self.assertEqual(result["open_quantity"], baseline.ledger.quantity)

    def test_loss_causes_independent_account_divergence(self):
        result = self.compare()
        baseline = result["baseline"]
        intervention = result["intervention"]

        self.assertEqual(baseline["fills"][:2], intervention["fills"])
        self.assertEqual(len(baseline["fills"]), 3)
        self.assertEqual(len(intervention["fills"]), 2)
        self.assertGreater(baseline["open_quantity"], 0)
        self.assertEqual(intervention["open_quantity"], 0)
        self.assertEqual(
            baseline["net_realized_usd"],
            intervention["net_realized_usd"],
        )
        self.assertIsNone(baseline["cooldown_until"])
        self.assertIsNotNone(intervention["cooldown_until"])

    def test_intervention_can_reenter_at_boundary(self):
        rows = self.inputs()
        boundary = NOW + timedelta(minutes=71)
        rows.append({
            "snapshot": snapshot(boundary),
            "decision_at": boundary,
            "risk_only": False,
        })
        result = self.compare(rows)
        event = result["intervention"]["events"][-1]

        self.assertTrue(event["executed"])
        self.assertEqual(event["action"], "buy")
        self.assertEqual(len(result["intervention"]["fills"]), 3)

    def test_no_loss_preserves_identical_results(self):
        rows = self.inputs(losing=False)
        # Recovery exits occur on regular cycles.
        rows[3]["risk_only"] = False
        result = self.compare(rows)

        self.assertEqual(result["baseline"], result["intervention"])
        self.assertIsNone(result["intervention"]["cooldown_until"])
        self.assertEqual(result["baseline"]["closed_trade_count"], 1)
        self.assertGreater(result["baseline"]["net_realized_usd"], 0)

    def test_summary_reconciles_with_completed_fills(self):
        result = self.compare()
        for name in ("baseline", "intervention"):
            account = result[name]
            pnls = [
                fill["realized_pnl"]
                for fill in account["fills"]
                if fill["side"] == "sell"
            ]
            self.assertEqual(account["closed_trade_count"], len(pnls))
            self.assertEqual(
                account["net_realized_usd"], sum(pnls, Decimal("0"))
            )
            self.assertEqual(
                account["gross_profit_usd"] - account["gross_loss_usd"],
                account["net_realized_usd"],
            )
            self.assertEqual(account["profit_factor"], Decimal("0"))
            self.assertEqual(account["profit_factor_status"], "defined")

    def test_inputs_and_results_are_independent(self):
        rows = self.inputs()
        before = deepcopy(rows)
        result = self.compare(rows)

        self.assertEqual(rows, before)
        result["baseline"]["events"][0]["reasons"].append("changed")
        result["baseline"]["fills"][0]["fee"] = Decimal("999")

        self.assertNotIn(
            "changed", result["intervention"]["events"][0]["reasons"]
        )
        self.assertNotEqual(
            result["intervention"]["fills"][0]["fee"], Decimal("999")
        )
        self.assertEqual(rows, before)
        self.assertEqual(self.compare(rows), self.compare(rows))

    def test_later_loss_cannot_change_earlier_decisions(self):
        rows = self.inputs()
        prefix = self.compare(rows[:3])
        full = self.compare(rows)

        for name in ("baseline", "intervention"):
            self.assertEqual(
                prefix[name]["events"], full[name]["events"][:3]
            )
        self.assertIsNone(prefix["intervention"]["cooldown_until"])

    def test_invalid_decision_inputs_are_rejected(self):
        cases = [
            [],
            tuple(self.inputs()),
            self.inputs() + [self.inputs()[-1]],
        ]
        future = self.inputs()
        future[0]["snapshot"] = snapshot(
            future[0]["decision_at"], age=-1
        )
        cases.append(future)

        boolean = self.inputs()
        boolean[0]["risk_only"] = 1
        cases.append(boolean)

        extra = self.inputs()
        extra[0]["extra"] = True
        cases.append(extra)

        for rows in cases:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.compare(rows)

    def test_comparison_grants_no_authority(self):
        result = self.compare()
        for field in (
            "validation_ready",
            "promotion_authorized",
            "strategy_change_authorized",
            "live_capital_authorized",
        ):
            self.assertIs(result[field], False)

        self.assertEqual(result["cooldown_seconds"], 3600)
        self.assertEqual(result["fee_bps"], Decimal("5"))
        self.assertEqual(result["slippage_bps"], Decimal("5"))


if __name__ == "__main__":
    unittest.main()
