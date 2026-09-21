"""Isolated service tests using real valuation and concentration functions."""

from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import patch

from app.capital import portfolio_intelligence_service as service


STAMP = "2026-09-21T12:00:00+00:00"


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
        "observations": [{
            "id": 10,
            "asset_id": 7,
            "provider": "CoinGecko",
            "observed_at": "2026-09-21T11:55:00+00:00",
            "price_usd": "10",
        }],
        "transactions": [],
    }


class IntelligenceServiceTests(unittest.TestCase):
    def setUp(self):
        self.data = snapshot()
        self.resolved = {
            1: {
                "experiment_id": "test_experiment",
                "strategy_name": "test_strategy",
                "portfolio_name": "Test Paper",
            },
        }

        resolver = patch.object(
            service, "_resolve_portfolios", return_value=self.resolved
        )
        reader = patch.object(
            service, "read_portfolio_inputs", return_value=self.data
        )
        market = patch.object(
            service,
            "_market_context",
            return_value={"status": "uncertain"},
        )

        self.resolver = resolver.start()
        self.reader = reader.start()
        self.market = market.start()
        self.addCleanup(resolver.stop)
        self.addCleanup(reader.stop)
        self.addCleanup(market.stop)

    def test_combines_valuations_concentration_and_strategy_identity(self):
        result = service.get_portfolio_intelligence()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["snapshot_at"], STAMP)

        account = result["valuations"]["portfolios"][0]
        self.assertEqual(account["valuation_status"], "indicative")
        self.assertEqual(account["total_value_usd"], "120")
        self.assertEqual(account["strategy_name"], "test_strategy")
        self.assertEqual(account["experiment_id"], "test_experiment")

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

    def test_observation_age_policy_is_twenty_minutes(self):
        with patch.object(
            service,
            "value_portfolio_snapshot",
            wraps=service.value_portfolio_snapshot,
        ) as valuator:
            service.get_portfolio_intelligence()

        self.assertEqual(
            valuator.call_args.kwargs["maximum_observation_age"],
            timedelta(minutes=20),
        )

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

    def test_ctva_provider_is_explicit_but_missing_prices_stay_missing(self):
        self.data["assets"][0].update(symbol="CTVA", asset_type="stock")
        self.data["observations"] = []

        with patch.object(
            service,
            "value_portfolio_snapshot",
            wraps=service.value_portfolio_snapshot,
        ) as valuator:
            result = service.get_portfolio_intelligence()

        self.assertEqual(
            valuator.call_args.kwargs["provider_by_asset"],
            {7: "Finnhub Promoted Attention"},
        )
        self.assertIsNone(
            result["valuations"]["portfolios"][0]["total_value_usd"]
        )

    def test_historical_metrics_are_explicitly_unavailable(self):
        result = service.get_portfolio_intelligence()
        self.assertEqual(
            set(result["historical_metrics"]),
            {"correlation", "drawdown_overlap", "risk_contribution"},
        )
        for metric in result["historical_metrics"].values():
            self.assertEqual(metric["status"], "unavailable")
            self.assertTrue(metric["reasons"])

    def test_regime_is_labelled_as_separate_context(self):
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
        cases = [
            [],
            [{**original, "id": 2}],
            [deepcopy(original), deepcopy(original)],
        ]
        for portfolios in cases:
            with self.subTest(portfolios=portfolios):
                self.data["portfolios"] = portfolios
                with self.assertRaisesRegex(ValueError, "requested set"):
                    service.get_portfolio_intelligence()

    def test_reader_failure_propagates_without_fabricated_result(self):
        self.reader.side_effect = RuntimeError("snapshot unavailable")
        with self.assertRaisesRegex(RuntimeError, "snapshot unavailable"):
            service.get_portfolio_intelligence()
        self.market.assert_not_called()

    def test_input_snapshot_and_registry_mapping_are_preserved(self):
        original = deepcopy((self.data, self.resolved))
        service.get_portfolio_intelligence()
        self.assertEqual((self.data, self.resolved), original)

    def test_authority_boundaries_are_explicit(self):
        result = service.get_portfolio_intelligence()
        for field in (
            "database_writes",
            "execution_authorized",
            "allocation_authority",
            "live_capital_authority",
        ):
            with self.subTest(field=field):
                self.assertFalse(result[field])
        self.assertTrue(result["human_approval_required"])


if __name__ == "__main__":
    unittest.main()
