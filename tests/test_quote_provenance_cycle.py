"""Collection-cycle tests with all external operations mocked."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from app.capital import quote_provenance_cycle as cycle


STAMP = "2026-09-25T12:00:00+00:00"


def universe():
    return {
        "snapshot_at": STAMP,
        "held_asset_count": 2,
        "assets": [
            {
                "id": 2,
                "symbol": "BTC",
                "asset_type": "crypto",
                "provider_id": "bitcoin",
            },
            {
                "id": 212,
                "symbol": "CTVA",
                "asset_type": "stock",
                "provider_id": None,
            },
        ],
        "unsupported_assets": [],
    }


class ProvenanceCycleTests(unittest.TestCase):
    def setUp(self):
        self.universe = universe()
        self.crypto_result = {
            "status": "captured",
            "request_attempted": True,
            "outcomes": [{
                "asset_id": 2,
                "symbol": "BTC",
                "status": "captured",
                "record_id": "a" * 64,
            }],
        }
        self.stock_result = {
            "asset_id": 212,
            "symbol": "CTVA",
            "record_id": "b" * 64,
        }

        patches = [
            patch.object(
                cycle, "read_collection_universe",
                return_value=self.universe,
            ),
            patch.object(
                cycle, "capture_crypto_batch",
                return_value=self.crypto_result,
            ),
            patch.object(
                cycle, "capture_quote_provenance",
                return_value=self.stock_result,
            ),
            patch.dict(cycle.os.environ, {"FINNHUB_API_KEY": "test-secret"}),
        ]
        self.reader, self.crypto, self.stock, _ = [
            item.start() for item in patches
        ]
        for item in patches:
            self.addCleanup(item.stop)

    def test_complete_cycle_captures_each_selected_asset(self):
        result = cycle.collect_held_quote_provenance()
        self.assertEqual(result["status"], "captured")
        self.assertEqual(result["captured_count"], 2)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(
            [row["asset_id"] for row in result["outcomes"]], [2, 212]
        )
        self.crypto.assert_called_once()
        self.stock.assert_called_once()
        self.assertEqual(
            self.crypto.call_args.kwargs["assets"],
            [self.universe["assets"][0]],
        )
        self.assertEqual(
            self.stock.call_args.kwargs["asset"],
            self.universe["assets"][1],
        )

    def test_all_crypto_assets_use_one_batch_call(self):
        self.universe["assets"].insert(1, {
            "id": 7,
            "symbol": "ETH",
            "asset_type": "crypto",
            "provider_id": "ethereum",
        })
        self.universe["held_asset_count"] = 3
        self.crypto_result["outcomes"].append({
            "asset_id": 7,
            "symbol": "ETH",
            "status": "captured",
            "record_id": "c" * 64,
        })

        result = cycle.collect_held_quote_provenance()

        self.crypto.assert_called_once()
        self.assertEqual(len(self.crypto.call_args.kwargs["assets"]), 2)
        self.assertEqual(result["captured_count"], 3)

    def test_empty_holdings_make_no_provider_calls(self):
        self.universe["assets"] = []
        self.universe["held_asset_count"] = 0

        result = cycle.collect_held_quote_provenance()

        self.assertEqual(result["status"], "empty")
        self.crypto.assert_not_called()
        self.stock.assert_not_called()

    def test_missing_stock_credentials_keep_crypto_success(self):
        with patch.dict(cycle.os.environ, {"FINNHUB_API_KEY": ""}):
            result = cycle.collect_held_quote_provenance()

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["captured_count"], 1)
        self.assertEqual(result["stock_capture_attempts"], 0)
        self.assertEqual(
            result["outcomes"][1]["reason"],
            "finnhub_credentials_unavailable",
        )
        self.stock.assert_not_called()

    def test_stock_failure_does_not_discard_crypto_success(self):
        self.stock.side_effect = RuntimeError("private provider detail")
        result = cycle.collect_held_quote_provenance()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["captured_count"], 1)
        self.assertEqual(result["failed_count"], 1)
        self.assertNotIn("private provider detail", str(result))

    def test_crypto_failure_does_not_prevent_stock_capture(self):
        self.crypto_result.update(
            status="failed",
            outcomes=[{
                "asset_id": 2,
                "symbol": "BTC",
                "status": "failed",
                "reason": "provider_request_or_response_failed",
            }],
        )
        result = cycle.collect_held_quote_provenance()
        self.assertEqual(result["status"], "partial")
        self.stock.assert_called_once()
        self.assertEqual(result["captured_count"], 1)

    def test_unexpected_crypto_error_has_unknown_request_state(self):
        self.crypto.side_effect = RuntimeError("token=test-secret")
        result = cycle.collect_held_quote_provenance()
        self.assertIsNone(result["crypto_request_attempted"])
        self.assertEqual(
            result["outcomes"][0]["reason"],
            "crypto_batch_failed_records_may_exist",
        )
        self.assertNotIn("test-secret", str(result))
        self.stock.assert_called_once()

    def test_all_failed_captures_report_failed(self):
        self.crypto.side_effect = RuntimeError("failed")
        self.stock.side_effect = RuntimeError("failed")
        result = cycle.collect_held_quote_provenance()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["captured_count"], 0)
        self.assertEqual(result["failed_count"], 2)

    def test_unsupported_assets_prevent_complete_success(self):
        self.universe["held_asset_count"] = 3
        self.universe["unsupported_assets"] = [{
            "asset_id": 99,
            "symbol": "UNKNOWN",
            "reason": "unsupported_asset_type",
        }]
        result = cycle.collect_held_quote_provenance()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["captured_count"], 2)
        self.assertEqual(len(result["unsupported_assets"]), 1)

    def test_unsupported_only_universe_makes_no_provider_calls(self):
        self.universe["assets"] = []
        self.universe["held_asset_count"] = 1
        self.universe["unsupported_assets"] = [{
            "asset_id": 99,
            "reason": "unsupported_asset_type",
        }]
        result = cycle.collect_held_quote_provenance()
        self.assertEqual(result["status"], "failed")
        self.crypto.assert_not_called()
        self.stock.assert_not_called()

    def test_universe_read_failure_prevents_all_capture(self):
        self.reader.side_effect = ValueError("portfolio mismatch")
        with self.assertRaisesRegex(ValueError, "portfolio mismatch"):
            cycle.collect_held_quote_provenance()
        self.crypto.assert_not_called()
        self.stock.assert_not_called()

    def test_crypto_only_universe_needs_no_stock_credentials(self):
        self.universe["assets"] = self.universe["assets"][:1]
        self.universe["held_asset_count"] = 1
        with patch.dict(cycle.os.environ, {"FINNHUB_API_KEY": ""}):
            result = cycle.collect_held_quote_provenance()
        self.assertEqual(result["status"], "captured")
        self.assertEqual(result["stock_capture_attempts"], 0)
        self.stock.assert_not_called()

    def test_stock_only_universe_makes_no_crypto_request(self):
        self.universe["assets"] = self.universe["assets"][1:]
        self.universe["held_asset_count"] = 1
        result = cycle.collect_held_quote_provenance()
        self.assertEqual(result["status"], "captured")
        self.assertFalse(result["crypto_request_attempted"])
        self.crypto.assert_not_called()

    def test_input_universe_is_preserved(self):
        original = deepcopy(self.universe)
        cycle.collect_held_quote_provenance()
        self.assertEqual(self.universe, original)

    def test_success_does_not_claim_eligibility_or_authority(self):
        result = cycle.collect_held_quote_provenance()
        for field in (
            "timestamp_eligibility_assessed",
            "database_writes",
            "legacy_observation_writes",
            "validation_receipt_writes",
            "execution_authorized",
            "live_capital_authorized",
        ):
            self.assertFalse(result[field])
        self.assertNotIn("test-secret", str(result))

    def test_keyboard_interrupt_is_not_hidden(self):
        self.crypto.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            cycle.collect_held_quote_provenance()
        self.stock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
