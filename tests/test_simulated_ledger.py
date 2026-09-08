import unittest
from copy import deepcopy
from decimal import Decimal as D

from app.capital.simulated_ledger import SimulatedLedger


class LedgerTests(unittest.TestCase):
    def test_zero_cost_round_trip(self):
        ledger = SimulatedLedger(1000, 0, 0)
        ledger.buy(quantity=1, reference_price=100)
        self.assertEqual(ledger.cash, D("900"))
        self.assertEqual(ledger.mark(110)["unrealized_pnl"], D("10"))
        ledger.sell(reference_price=110)
        self.assertEqual(ledger.cash, D("1010"))
        self.assertEqual(ledger.realized_pnl, D("10"))
        self.assertEqual(ledger.quantity, 0)
        ledger.mark(110)

    def test_fees_and_adverse_slippage_both_sides(self):
        ledger = SimulatedLedger(1000, 10, 10)
        buy = ledger.buy(quantity=1, reference_price=100)
        self.assertEqual(buy["fill_price"], D("100.1"))
        self.assertEqual(buy["fee"], D("0.1001"))
        sell = ledger.sell(reference_price=100)
        self.assertEqual(sell["fill_price"], D("99.9"))
        self.assertEqual(sell["fee"], D("0.0999"))
        self.assertEqual(ledger.realized_pnl, D("-0.4"))
        self.assertEqual(ledger.cash, D("999.6"))
        self.assertEqual(ledger.total_fees, D("0.2"))
        ledger.mark(100)

    def test_duplicate_buy_rejected_without_mutation(self):
        ledger = SimulatedLedger(1000, 0, 0)
        ledger.buy(quantity=1, reference_price=100)
        before = deepcopy(vars(ledger))
        with self.assertRaises(ValueError):
            ledger.buy(quantity=1, reference_price=100)
        self.assertEqual(vars(ledger), before)

    def test_unaffordable_fill_rejected_without_mutation(self):
        ledger = SimulatedLedger(100, 10, 10)
        before = deepcopy(vars(ledger))
        with self.assertRaises(ValueError):
            ledger.buy(quantity=1, reference_price=100)
        self.assertEqual(vars(ledger), before)

    def test_invalid_values_and_empty_sale(self):
        for value in ("NaN", "Infinity", "-1", "0"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    SimulatedLedger(value, 0, 0)
        with self.assertRaises(ValueError):
            SimulatedLedger(1000, 0, 10000)
        with self.assertRaises(ValueError):
            SimulatedLedger(1000, 0, 0).sell(reference_price=100)

    def test_open_position_mark_and_repeatability(self):
        def run():
            ledger = SimulatedLedger(1000, 5, 8)
            ledger.buy(quantity="0.987654321012", reference_price="101.23")
            first = ledger.mark("98.76")
            ledger.sell(reference_price="102.34")
            return first, ledger.mark("102.34"), ledger.fills
        self.assertEqual(run(), run())


if __name__ == "__main__":
    unittest.main()
