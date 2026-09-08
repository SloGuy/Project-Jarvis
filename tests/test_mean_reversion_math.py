import unittest
from decimal import Decimal
from unittest.mock import patch

from app.capital.mean_reversion_math import calculate_mean_reversion_snapshot
from app.autonomous_trading import mean_reversion_v2_strategy as strategy

STAMP = "2026-09-01T00:00:00+00:00"


def rows(prices):
    return [{"price_usd": p, "observed_at": STAMP} for p in prices]


class SnapshotTests(unittest.TestCase):
    def test_known_population_statistics(self):
        result = calculate_mean_reversion_snapshot(
            symbol=" test ", observations=rows([90] + [100] * 19)
        )
        self.assertEqual(result.symbol, "TEST")
        self.assertEqual(result.mean_price_usd, Decimal("99.5"))
        deviation = Decimal("4.75").sqrt()
        self.assertEqual(result.standard_deviation_usd, deviation)
        self.assertEqual(result.z_score, Decimal("-9.5") / deviation)
        self.assertTrue(result.usable)

    def test_insufficient_and_constant_prices(self):
        for prices in ([], [100] * 19, [100] * 20):
            result = calculate_mean_reversion_snapshot(
                symbol="TEST", observations=rows(prices)
            )
            self.assertFalse(result.usable)
            self.assertIsNone(result.z_score)

    def test_loader_uses_shared_calculation(self):
        observations = rows([90] + [100] * 19)
        with patch.object(
            strategy, "get_market_history",
            return_value={"observations": observations},
        ) as history, patch.object(
            strategy, "calculate_mean_reversion_snapshot",
            wraps=calculate_mean_reversion_snapshot,
        ) as calculate:
            result = strategy.get_mean_reversion_snapshot(symbol="test")
        history.assert_called_once_with(symbol="TEST", limit=48)
        calculate.assert_called_once_with(
            symbol="TEST", observations=observations
        )
        self.assertEqual(result.observation_count, 20)


if __name__ == "__main__":
    unittest.main()
