from decimal import Decimal as D
import runpy
import unittest

from app.capital.shadow_coordinator import ShadowCoordinator, allocation_quantity
from app.capital.policies import MEAN_REVERSION_V2_1000_POLICY as POLICY

fixture = runpy.run_path("tests/test_shadow_coordinator.py")


class AllocationSizingTests(unittest.TestCase):
    def make(self, cap="5.89"):
        return ShadowCoordinator(
            policy=POLICY,
            strategy_caps={
                "mean_reversion_v2": cap,
                "volatility_breakout_v1": "0",
            },
            fee_bps="5", slippage_bps="5",
        )

    def test_real_evaluator_entry_fits_smaller_cap(self):
        coordinator = self.make()
        for minute in (0, 5, 10):
            result = fixture["CoordinatorTests"].tick(self, coordinator, minute)
        buys = [event for event in result["events"] if event["side"] == "buy"]
        self.assertEqual(len(buys), 1)
        self.assertTrue(buys[0]["executed"])
        self.assertEqual(buys[0]["strategy"], "mean_reversion_v2")
        self.assertNotIn(
            ("volatility_breakout_v1", "SPY"), coordinator.ledger.positions
        )
        account = coordinator.ledger.mark({"SPY": D("98")})
        value = account["strategy_attribution"]["mean_reversion_v2"]["market_value"]
        self.assertLessEqual(value / account["total_value_usd"] * 100, D("5.89"))
        self.assertGreater(value, D("58"))

    def test_zero_capacity_and_existing_allocation(self):
        coordinator = self.make()
        ledger = coordinator.ledger
        self.assertEqual(allocation_quantity(
            ledger, strategy="volatility_breakout_v1",
            price=D("100"), prices={}, requested_percent=D("10"),
        ), 0)
        before = allocation_quantity(
            ledger, strategy="mean_reversion_v2",
            price=D("100"), prices={}, requested_percent=D("10"),
        )
        from datetime import timedelta
        result = ledger.execute(
            order_id="existing", strategy="mean_reversion_v2",
            symbol="SPY", side="buy", quantity=D("0.3"),
            decision_at=fixture["NOW"],
            quotes={"SPY": (D("100"), fixture["NOW"] - timedelta(seconds=1))},
        )
        self.assertTrue(result["executed"])
        after = allocation_quantity(
            ledger, strategy="mean_reversion_v2",
            price=D("100"), prices={"SPY": D("100")}, requested_percent=D("10"),
        )
        self.assertLess(after, before)
        self.assertGreaterEqual(after, 0)

    def test_sizing_does_not_increase_requested_position(self):
        ledger = self.make(cap="15").ledger
        quantity = allocation_quantity(
            ledger, strategy="mean_reversion_v2",
            price=D("100"), prices={}, requested_percent=D("2"),
        )
        self.assertLessEqual(quantity, D("0.2"))


if __name__ == "__main__":
    unittest.main()
