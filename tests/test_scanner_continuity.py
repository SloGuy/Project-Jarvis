from contextlib import ExitStack
from copy import deepcopy
import unittest
from unittest.mock import patch

from app import broad_market_scanner as scanner


class ScannerContinuityTests(unittest.TestCase):
    def scan(self, previous_timestamp):
        observations = {"SPY": [{
            "quote_timestamp": previous_timestamp,
            "change_percent": 2.0,
        }]}
        quote = {
            "symbol": "SPY", "available": True, "price_usd": 100.0,
            "change_percent": 2.0, "quote_timestamp": 100,
        }
        with ExitStack() as stack:
            stack.enter_context(patch.dict("os.environ", {"FINNHUB_API_KEY": "synthetic"}))
            stack.enter_context(patch.object(
                scanner, "get_scan_batch", return_value=(("SPY",), 0, 1)
            ))
            stack.enter_context(patch.object(
                scanner, "get_broad_stock_symbols", return_value=("SPY", "QQQ")
            ))
            stack.enter_context(patch.object(
                scanner, "_load_observations", return_value=observations
            ))
            stack.enter_context(patch.object(
                scanner, "fetch_stock_quote", return_value=deepcopy(quote)
            ))
            stack.enter_context(patch.object(
                scanner.requests, "get", side_effect=AssertionError("Network forbidden")
            ))
            stack.enter_context(patch.object(
                scanner, "evaluate_promotion", return_value={
                    "eligible": True, "reasons": ["Synthetic eligibility"],
                }
            ))
            promote = stack.enter_context(patch.object(
                scanner, "promote_asset", return_value={"promotion": "synthetic"}
            ))
            save = stack.enter_context(patch.object(scanner, "_save_observations"))
            stack.enter_context(patch.object(scanner, "_save_cursor"))
            stack.enter_context(patch.object(scanner, "_save_latest_scan"))
            result = scanner.scan_broad_market(batch_size=1)
            return result, promote.call_count, deepcopy(save.call_args.args[0])

    def test_fresh_quote_reaches_attention_promotion(self):
        result, promoted, observations = self.scan(99)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["available_count"], 1)
        self.assertEqual(result["promotion_candidate_count"], 1)
        self.assertEqual(promoted, 1)
        self.assertEqual(len(observations["SPY"]), 2)

    def test_repeated_quote_does_not_repeat_promotion(self):
        result, promoted, observations = self.scan(100)
        self.assertEqual(result["promotion_candidate_count"], 0)
        self.assertEqual(promoted, 0)
        self.assertEqual(len(observations["SPY"]), 1)


if __name__ == "__main__":
    unittest.main()
