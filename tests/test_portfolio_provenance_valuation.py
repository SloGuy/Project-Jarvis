"""Integration tests using temporary quote provenance files."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.capital.portfolio_provenance_valuation import (
    value_provenance_snapshot,
)
from app.capital.quote_provenance import make_quote_provenance
from app.capital.quote_provenance_store import save_quote_provenance


NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


class PortfolioProvenanceValuationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.snapshot = {
            "snapshot_at": NOW.isoformat(),
            "assets": [
                {"id": 2, "symbol": "BTC", "asset_type": "crypto"},
            ],
            "portfolios": [{
                "id": 3,
                "name": "Test Paper",
                "portfolio_type": "paper",
                "cash_balance_usd": "100.00000001",
            }],
            "positions": [{
                "portfolio_id": 3,
                "asset_id": 2,
                "quantity": "0.123456789123",
            }],
        }

    def save(
        self,
        *,
        price="123.12345678",
        capture_age=10,
        provider_age=30,
        missing_time=False,
        asset_id=2,
        symbol="BTC",
    ):
        record = make_quote_provenance(
            asset_id=asset_id,
            symbol=symbol,
            asset_type="crypto",
            provider="CoinGecko REST",
            price_usd=price,
            provider_timestamp=(
                None if missing_time
                else int((NOW - timedelta(seconds=provider_age)).timestamp())
            ),
            captured_at=NOW - timedelta(seconds=capture_age),
        )
        return save_quote_provenance(
            directory=self.directory,
            record=record,
        )

    def value(self, providers=None):
        return value_provenance_snapshot(
            snapshot=self.snapshot,
            directory=self.directory,
            provider_by_asset=(
                {2: "CoinGecko REST"} if providers is None else providers
            ),
            maximum_provider_age=timedelta(minutes=20),
            maximum_capture_age=timedelta(minutes=2),
        )

    def test_fresh_record_values_with_exact_precision(self):
        saved = self.save()
        result = self.value()
        portfolio = result["portfolios"][0]
        expected = (
            Decimal("100.00000001")
            + Decimal("0.123456789123") * Decimal("123.12345678")
        )
        self.assertEqual(Decimal(portfolio["total_value_usd"]), expected)
        self.assertEqual(portfolio["valuation_status"], "indicative")
        holding = portfolio["holdings"][0]
        self.assertEqual(
            holding["quote_provenance"]["record_id"], saved["record_id"]
        )
        assessment = holding["quote_provenance"]["assessment"]
        self.assertNotEqual(
            assessment["provider_observed_at"], assessment["captured_at"]
        )
        self.assertEqual(
            holding["observed_at"], assessment["provider_observed_at"]
        )

    def test_missing_quote_withholds_total(self):
        portfolio = self.value()["portfolios"][0]
        self.assertEqual(portfolio["valuation_status"], "incomplete")
        self.assertIsNone(portfolio["total_value_usd"])
        self.assertEqual(
            portfolio["holdings"][0]["exclusion_reason"], "missing"
        )

    def test_recent_capture_does_not_refresh_old_provider_quote(self):
        self.save(provider_age=1201)
        portfolio = self.value()["portfolios"][0]
        self.assertIsNone(portfolio["total_value_usd"])
        reasons = portfolio["holdings"][0]["quote_provenance"]["assessment"][
            "reasons"
        ]
        self.assertIn("provider_quote_too_old", reasons)

    def test_old_capture_is_ineligible(self):
        self.save(capture_age=121, provider_age=130)
        portfolio = self.value()["portfolios"][0]
        self.assertIsNone(portfolio["total_value_usd"])
        reasons = portfolio["holdings"][0]["quote_provenance"]["assessment"][
            "reasons"
        ]
        self.assertIn("capture_too_old", reasons)

    def test_missing_provider_time_is_ineligible(self):
        self.save(missing_time=True)
        portfolio = self.value()["portfolios"][0]
        self.assertIsNone(portfolio["total_value_usd"])

    def test_latest_bad_record_does_not_fall_back(self):
        self.save(capture_age=20, provider_age=40)
        self.save(capture_age=10, provider_age=1300)
        portfolio = self.value()["portfolios"][0]
        self.assertIsNone(portfolio["total_value_usd"])
        self.assertEqual(
            portfolio["holdings"][0]["quote_provenance"]["status"],
            "ineligible",
        )

    def test_conflicting_same_time_records_are_ambiguous(self):
        self.save(price="100")
        self.save(price="101")
        portfolio = self.value()["portfolios"][0]
        self.assertIsNone(portfolio["total_value_usd"])
        self.assertEqual(
            portfolio["holdings"][0]["quote_provenance"]["status"],
            "ambiguous",
        )

    def test_future_capture_is_excluded(self):
        self.save(capture_age=-10, provider_age=0)
        result = self.value()
        self.assertEqual(result["quote_coverage"]["future_capture_count"], 1)
        self.assertIsNone(result["portfolios"][0]["total_value_usd"])

    def test_corrupted_record_is_rejected(self):
        saved = self.save()
        path = Path(saved["path"])
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaises(ValueError):
            self.value()

    def test_unrelated_corruption_is_not_silently_ignored(self):
        self.save()
        saved = self.save(asset_id=7, symbol="ETH")
        Path(saved["path"]).write_text("{}")
        with self.assertRaises(ValueError):
            self.value()

    def test_wrong_saved_symbol_is_rejected(self):
        self.save(symbol="ETH")
        with self.assertRaises(ValueError):
            self.value()

    def test_missing_provider_selection_withholds_total(self):
        self.save()
        portfolio = self.value(providers={})["portfolios"][0]
        self.assertIsNone(portfolio["total_value_usd"])
        self.assertEqual(
            portfolio["holdings"][0]["coverage_note"],
            "provider_not_selected",
        )

    def test_missing_metadata_withholds_total(self):
        self.save()
        self.snapshot["assets"] = []
        portfolio = self.value()["portfolios"][0]
        self.assertIsNone(portfolio["total_value_usd"])
        self.assertEqual(
            portfolio["holdings"][0]["coverage_note"],
            "missing_asset_metadata",
        )

    def test_cash_only_portfolio_needs_no_quote(self):
        self.snapshot["positions"] = []
        portfolio = self.value()["portfolios"][0]
        self.assertEqual(portfolio["valuation_status"], "indicative")
        self.assertEqual(
            Decimal(portfolio["total_value_usd"]), Decimal("100.00000001")
        )

    def test_zero_quantity_does_not_require_quote(self):
        self.snapshot["positions"][0]["quantity"] = "0"
        result = self.value()
        self.assertEqual(result["quote_coverage"]["requested_count"], 0)
        self.assertEqual(
            result["portfolios"][0]["valuation_status"], "indicative"
        )

    def test_duplicate_positions_are_rejected(self):
        self.snapshot["positions"].append(
            deepcopy(self.snapshot["positions"][0])
        )
        with self.assertRaises(ValueError):
            self.value()

    def test_negative_quantity_is_rejected(self):
        self.snapshot["positions"][0]["quantity"] = "-1"
        with self.assertRaises(ValueError):
            self.value()

    def test_live_portfolio_is_rejected(self):
        self.snapshot["portfolios"][0]["portfolio_type"] = "live"
        with self.assertRaises(ValueError):
            self.value()

    def test_one_missing_holding_prevents_partial_total(self):
        self.save()
        self.snapshot["assets"].append(
            {"id": 7, "symbol": "ETH", "asset_type": "crypto"}
        )
        self.snapshot["positions"].append(
            {"portfolio_id": 3, "asset_id": 7, "quantity": "1"}
        )
        portfolio = self.value({
            2: "CoinGecko REST",
            7: "CoinGecko REST",
        })["portfolios"][0]
        self.assertGreater(Decimal(portfolio["known_market_value_usd"]), 0)
        self.assertIsNone(portfolio["total_value_usd"])

    def test_inputs_and_files_remain_unchanged(self):
        self.save()
        original = deepcopy(self.snapshot)
        files = {
            path.name: path.read_bytes()
            for path in self.directory.iterdir()
        }
        result = self.value()
        self.assertEqual(self.snapshot, original)
        self.assertEqual(files, {
            path.name: path.read_bytes()
            for path in self.directory.iterdir()
        })
        for name in (
            "same_database_and_filesystem_snapshot",
            "market_quote_freshness_verified",
            "historical_completeness_verified",
            "historical_availability_verified",
            "database_writes",
            "allocation_authority",
            "live_capital_authority",
        ):
            self.assertIs(result[name], False)


if __name__ == "__main__":
    unittest.main()
