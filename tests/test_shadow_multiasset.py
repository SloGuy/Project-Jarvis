from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import unittest

from app.capital.shared_shadow_ledger import SharedShadowLedger
from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY as POLICY

NOW = datetime(2026, 9, 4, 14, tzinfo=timezone.utc)
QUOTES = {
    "SPY": (D("100"), NOW - timedelta(seconds=1)),
    "BTC": (D("1000"), NOW - timedelta(seconds=1)),
}


class MultiAssetTests(unittest.TestCase):
    def make(self, **limits):
        return SharedShadowLedger(
            policy=replace(POLICY, **limits),
            strategy_caps={"mean_reversion_v2": "15", "volatility_breakout_v1": "15"},
            fee_bps="0", slippage_bps="0",
        )

    def buy(self, ledger, ident, strategy, symbol, quantity, quotes=None):
        return ledger.execute(
            order_id=ident, strategy=strategy, symbol=symbol, side="buy",
            quantity=quantity, decision_at=NOW,
            quotes=QUOTES if quotes is None else quotes,
        )

    def test_distinct_assets_share_cash_and_reconcile(self):
        ledger = self.make()
        self.assertTrue(self.buy(
            ledger, "one", "mean_reversion_v2", "SPY", "1"
        )["executed"])
        self.assertTrue(self.buy(
            ledger, "two", "volatility_breakout_v1", "BTC", "0.1"
        )["executed"])
        account = ledger.mark({"SPY": D("110"), "BTC": D("900")})
        self.assertEqual(account["position_count"], 2)
        self.assertEqual(account["cash_balance_usd"], 800)
        self.assertEqual(account["total_value_usd"], 1000)
        self.assertEqual(
            account["strategy_attribution"]["mean_reversion_v2"]["unrealized_pnl"], 10
        )
        self.assertEqual(
            account["strategy_attribution"]["volatility_breakout_v1"]["unrealized_pnl"], -10
        )

    def test_position_count_limit(self):
        ledger = self.make(max_open_positions=1)
        self.buy(ledger, "one", "mean_reversion_v2", "SPY", "1")
        result = self.buy(ledger, "two", "volatility_breakout_v1", "BTC", "0.1")
        self.assertFalse(result["executed"])
        self.assertTrue(any("number of open positions" in r for r in result["reasons"]))

    def test_same_sector_and_correlation_are_combined(self):
        ledger = self.make(
            max_sector_exposure_percent=D("15"),
            max_correlation_group_exposure_percent=D("15"),
        )
        self.buy(ledger, "one", "mean_reversion_v2", "SPY", "1")
        result = self.buy(ledger, "two", "volatility_breakout_v1", "SPY", "1")
        self.assertFalse(result["executed"])
        self.assertTrue(any("sector exposure" in r for r in result["reasons"]))
        self.assertTrue(any("correlation-group" in r for r in result["reasons"]))

    def test_stale_other_holding_blocks_new_entry(self):
        ledger = self.make()
        self.buy(ledger, "one", "mean_reversion_v2", "SPY", "1")
        quotes = dict(QUOTES)
        quotes["SPY"] = (D("100"), NOW - timedelta(seconds=121))
        result = self.buy(
            ledger, "two", "volatility_breakout_v1", "BTC", "0.1", quotes
        )
        self.assertFalse(result["executed"])
        self.assertIn("Fresh valuation required for SPY.", result["reasons"])
        self.assertEqual(ledger.cash, 900)

    def test_missing_mark_cannot_partially_mutate_portfolio(self):
        ledger = self.make()
        self.buy(ledger, "one", "mean_reversion_v2", "SPY", "1")
        before = deepcopy((ledger.positions, ledger.cash, ledger.orders))
        with self.assertRaises(KeyError):
            self.buy(
                ledger, "two", "volatility_breakout_v1", "BTC", "0.1",
                {"BTC": QUOTES["BTC"]},
            )
        self.assertEqual((ledger.positions, ledger.cash, ledger.orders), before)


if __name__ == "__main__":
    unittest.main()
