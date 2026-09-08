from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import unittest

from app.capital.shared_shadow_ledger import SharedShadowLedger
from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY

NOW = datetime(2026, 9, 4, 14, tzinfo=timezone.utc)


class SharedLedgerTests(unittest.TestCase):
    def make(self, costs="0", **limits):
        return SharedShadowLedger(
            policy=replace(MEAN_REVERSION_V2_1000_POLICY, **limits),
            strategy_caps={"mean_reversion_v2": "15", "volatility_breakout_v1": "15"},
            fee_bps=costs, slippage_bps=costs,
        )

    def order(self, ledger, ident, strategy="mean_reversion_v2",
              side="buy", quantity="1", price="100", age=1):
        return ledger.execute(
            order_id=ident, strategy=strategy, symbol="SPY", side=side,
            quantity=quantity, decision_at=NOW,
            quotes={"SPY": (D(price), NOW - timedelta(seconds=age))},
        )

    def test_shared_symbol_exposure(self):
        ledger = self.make()
        self.assertTrue(self.order(ledger, "one")["executed"])
        self.assertTrue(self.order(
            ledger, "two", strategy="volatility_breakout_v1"
        )["executed"])
        account = ledger.mark({"SPY": D("100")})
        self.assertEqual(account["position_count"], 1)
        self.assertEqual(account["positions"][0]["quantity"], D("2"))
        self.assertEqual(account["cash_balance_usd"], D("800"))
        rejected = self.order(ledger, "three", quantity="0.1")
        self.assertFalse(rejected["executed"])
        self.assertTrue(any("position size" in r for r in rejected["reasons"]))

    def test_strategy_cap(self):
        ledger = self.make()
        result = self.order(ledger, "one", quantity="1.6")
        self.assertFalse(result["executed"])
        self.assertIn("Strategy allocation cap exceeded.", result["reasons"])
        self.assertEqual(ledger.cash, D("1000"))

    def test_ownership_and_partial_sale(self):
        ledger = self.make()
        self.order(ledger, "buy")
        wrong = self.order(
            ledger, "wrong", strategy="volatility_breakout_v1", side="sell"
        )
        self.assertFalse(wrong["executed"])
        first = self.order(ledger, "partial", side="sell", quantity="0.4", price="110")
        last = self.order(ledger, "rest", side="sell", quantity="0.6", price="110")
        self.assertTrue(first["executed"])
        self.assertTrue(last["executed"])
        self.assertEqual(ledger.realized["mean_reversion_v2"], D("10"))
        self.assertEqual(ledger.mark({})["total_value_usd"], D("1010"))

    def test_costs_reconcile(self):
        ledger = self.make(costs="5")
        self.assertTrue(self.order(ledger, "buy")["executed"])
        self.assertTrue(self.order(ledger, "sell", side="sell", price="110")["executed"])
        account = ledger.mark({})
        pnl = sum(ledger.realized.values())
        self.assertEqual(account["total_value_usd"] - D("1000"), pnl)
        self.assertGreater(ledger.fees["mean_reversion_v2"], 0)

    def test_idempotency_and_conflicting_retry(self):
        ledger = self.make()
        first = self.order(ledger, "same")
        self.assertEqual(first, self.order(ledger, "same"))
        self.assertEqual(ledger.cash, D("900"))
        with self.assertRaisesRegex(ValueError, "different instructions"):
            self.order(ledger, "same", quantity="0.5")

    def test_stale_and_fee_reserve(self):
        ledger = self.make()
        self.assertFalse(self.order(ledger, "stale", age=121)["executed"])
        ledger = self.make(
            costs="5", minimum_cash_reserve_percent=D("90")
        )
        self.assertFalse(self.order(ledger, "costly")["executed"])
        self.assertEqual(ledger.cash, D("1000"))

    def test_total_exposure_shared_across_strategies(self):
        ledger = self.make(max_total_exposure_percent=D("15"))
        self.assertTrue(self.order(ledger, "one")["executed"])
        self.assertFalse(self.order(
            ledger, "two", strategy="volatility_breakout_v1"
        )["executed"])
        self.assertEqual(ledger.cash, D("900"))


if __name__ == "__main__":
    unittest.main()
