"""Service integration tests with temporary saved provenance."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.capital import portfolio_intelligence_service as service
from app.capital.quote_provenance import make_quote_provenance
from app.capital.quote_provenance_store import save_quote_provenance


NOW = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
STAMP = NOW.isoformat()


def snapshot():
    return {
        "snapshot_at": STAMP,
        "portfolios": [{
            "id": 1,
            "name": "Test Paper",
            "portfolio_type": "paper",
            "is_active": True,
            "cash_balance_usd": "100",
        }],
        "positions": [{
            "portfolio_id": 1,
            "asset_id": 7,
            "quantity": "2",
        }],
        "assets": [{
            "id": 7,
            "symbol": "BTC",
            "asset_type": "crypto",
        }],
        # Deliberately differs from the provenance price.
        "observations": [{
            "id": 10,
            "asset_id": 7,
            "provider": "CoinGecko",
            "observed_at": STAMP,
            "price_usd": "999",
        }],
        "transactions": [],
    }


class IntelligenceServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.data = snapshot()
        self.resolved = {
            1: {
                "experiment_id": "test_experiment",
                "strategy_name": "test_strategy",
                "portfolio_name": "Test Paper",
            },
        }
        self.saved = self.save_quote()
        self.resolver = self.patch(
            "_resolve_portfolios", return_value=self.resolved
        )
        self.reader = self.patch(
            "read_portfolio_inputs", return_value=self.data
        )
        self.market = self.patch(
            "_market_context", return_value={"status": "uncertain"}
        )
        self.patch("PROVENANCE_DIRECTORY", new=self.directory)

    def patch(self, name, **kwargs):
        patcher = patch.object(service, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def save_quote(self, *, capture_age=30, provider_age=60):
        record = make_quote_provenance(
            asset_id=7,
            symbol="BTC",
            asset_type="crypto",
            provider="CoinGecko REST",
            price_usd="10",
            provider_timestamp=int(
                (NOW - timedelta(seconds=provider_age)).timestamp()
            ),
            captured_at=NOW - timedelta(seconds=capture_age),
        )
        return save_quote_provenance(
            directory=self.directory, record=record
        )

    def test_combines_real_provenance_valuation_and_concentration(self):
        result = service.get_portfolio_intelligence()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["snapshot_at"], STAMP)
        self.assertEqual(
            result["valuations"]["valuation_basis"],
            "saved_quote_provenance_indicative",
        )
        account = result["valuations"]["portfolios"][0]
        self.assertEqual(account["valuation_status"], "indicative")
        self.assertEqual(account["total_value_usd"], "120")
        self.assertEqual(account["strategy_name"], "test_strategy")
        self.assertEqual(account["experiment_id"], "test_experiment")
        self.assertEqual(
            account["holdings"][0]["quote_provenance"]["record_id"],
            self.saved["record_id"],
        )
        concentration = result["concentration"]["portfolios"][0]
        self.assertEqual(concentration["status"], "indicative")
        self.assertEqual(concentration["strategy_name"], "test_strategy")

    def test_reads_only_resolved_portfolios(self):
        service.get_portfolio_intelligence()
        self.reader.assert_called_once()
        self.assertEqual(
            self.reader.call_args.kwargs["portfolio_ids"], [1]
        )
        self.assertIsNotNone(
            self.reader.call_args.kwargs["quote_window_start"].utcoffset()
        )

    def test_explicit_provider_and_capture_age_limits(self):
        with patch.object(
            service,
            "value_provenance_snapshot",
            wraps=service.value_provenance_snapshot,
        ) as valuator:
            service.get_portfolio_intelligence()
        arguments = valuator.call_args.kwargs
        self.assertEqual(
            arguments["maximum_provider_age"], timedelta(minutes=20)
        )
        self.assertEqual(
            arguments["maximum_capture_age"], timedelta(minutes=2)
        )
        self.assertEqual(arguments["directory"], self.directory)

    def test_unknown_asset_remains_uncovered(self):
        self.data["assets"][0]["symbol"] = "UNKNOWN"
        result = service.get_portfolio_intelligence()
        account = result["valuations"]["portfolios"][0]
        self.assertEqual(account["valuation_status"], "incomplete")
        self.assertIsNone(account["total_value_usd"])
        self.assertEqual(
            result["concentration"]["combined"]["status"], "unavailable"
        )

    def test_provider_selection_uses_asset_type_and_symbol(self):
        self.data["assets"][0]["asset_type"] = "stock"
        result = service.get_portfolio_intelligence()
        holding = result["valuations"]["portfolios"][0]["holdings"][0]
        self.assertEqual(holding["coverage_note"], "provider_not_selected")

    def test_ctva_requires_explicit_finnhub_rest_provenance(self):
        self.data["assets"][0].update(symbol="CTVA", asset_type="stock")
        with patch.object(
            service,
            "value_provenance_snapshot",
            wraps=service.value_provenance_snapshot,
        ) as valuator:
            result = service.get_portfolio_intelligence()
        self.assertEqual(
            valuator.call_args.kwargs["provider_by_asset"],
            {7: "Finnhub REST"},
        )
        self.assertIsNone(
            result["valuations"]["portfolios"][0]["total_value_usd"]
        )

    def test_missing_provenance_does_not_use_legacy_observation(self):
        Path(self.saved["path"]).unlink()
        result = service.get_portfolio_intelligence()
        self.assertIsNone(
            result["valuations"]["portfolios"][0]["total_value_usd"]
        )

    def test_latest_stale_quote_does_not_use_older_good_quote(self):
        self.save_quote(capture_age=10, provider_age=1300)
        result = service.get_portfolio_intelligence()
        account = result["valuations"]["portfolios"][0]
        self.assertIsNone(account["total_value_usd"])
        assessment = account["holdings"][0]["quote_provenance"]["assessment"]
        self.assertIn("provider_quote_too_old", assessment["reasons"])

    def test_corrupt_record_propagates_without_fabricated_values(self):
        Path(self.saved["path"]).write_text("{}")
        with self.assertRaises(ValueError):
            service.get_portfolio_intelligence()
        self.market.assert_not_called()

    def test_historical_metrics_remain_unavailable(self):
        result = service.get_portfolio_intelligence()
        self.assertEqual(
            set(result["historical_metrics"]),
            {"correlation", "drawdown_overlap", "risk_contribution"},
        )
        for metric in result["historical_metrics"].values():
            self.assertEqual(metric["status"], "unavailable")
            self.assertTrue(metric["reasons"])

    def test_regime_remains_separate_context(self):
        result = service.get_portfolio_intelligence()
        context = result["market_context"]
        self.assertFalse(context["same_database_snapshot"])
        self.assertEqual(
            context["scope"], "market_regime_not_portfolio_regime_exposure"
        )
        self.assertEqual(context["report"], {"status": "uncertain"})
        self.market.assert_called_once()

    def test_changed_portfolio_identity_or_eligibility_is_rejected(self):
        for field, value in (
            ("name", "Different Portfolio"),
            ("portfolio_type", "live"),
            ("is_active", False),
        ):
            with self.subTest(field=field):
                self.data["portfolios"][0] = snapshot()["portfolios"][0]
                self.data["portfolios"][0][field] = value
                with self.assertRaisesRegex(ValueError, "eligibility changed"):
                    service.get_portfolio_intelligence()

    def test_missing_extra_or_duplicate_portfolios_are_rejected(self):
        original = snapshot()["portfolios"][0]
        for portfolios in (
            [],
            [{**original, "id": 2}],
            [deepcopy(original), deepcopy(original)],
        ):
            with self.subTest(portfolios=portfolios):
                self.data["portfolios"] = portfolios
                with self.assertRaisesRegex(ValueError, "requested set"):
                    service.get_portfolio_intelligence()

    def test_reader_failure_propagates(self):
        self.reader.side_effect = RuntimeError("snapshot unavailable")
        with self.assertRaisesRegex(RuntimeError, "snapshot unavailable"):
            service.get_portfolio_intelligence()
        self.market.assert_not_called()

    def test_inputs_are_preserved(self):
        original = deepcopy((self.data, self.resolved))
        service.get_portfolio_intelligence()
        self.assertEqual((self.data, self.resolved), original)

    def test_authority_boundaries_remain_explicit(self):
        result = service.get_portfolio_intelligence()
        for field in (
            "database_writes",
            "execution_authorized",
            "allocation_authority",
            "live_capital_authority",
        ):
            self.assertFalse(result[field])
        self.assertTrue(result["human_approval_required"])
        self.assertFalse(
            result["valuations"]["same_database_and_filesystem_snapshot"]
        )


if __name__ == "__main__":
    unittest.main()
