"""Checkpoint assembly tests without database or network access."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

from app.capital import portfolio_checkpoint_capture as capture


class CheckpointCaptureTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch as mock_patch
        context_patch = mock_patch(
            "app.capital.portfolio_checkpoint_capture.capture_market_context",
            return_value={
                "observed_at": "2026-09-27T08:40:00+00:00",
                "scope": "spy_market_proxy_not_portfolio_regime",
                "report": {"status": "unavailable"},
            },
        )
        context_patch.start()
        self.addCleanup(context_patch.stop)
        self.resolved = {
            3: {
                "portfolio_name": "Test Paper",
                "experiment_id": "experiment_test",
                "strategy_name": "test_strategy",
            },
        }
        self.audit = {
            "sampled_at": "2026-09-27T08:40:00+00:00",
            "installation": {"installation_id": "test-installation"},
            "events": [],
        }
        self.inputs = {
            "snapshot_at": self.audit["sampled_at"],
            "assets": [
                {"id": 2, "symbol": "BTC", "asset_type": "crypto"},
                {"id": 212, "symbol": "CTVA", "asset_type": "stock"},
                {"id": 999, "symbol": "UNKNOWN", "asset_type": "crypto"},
            ],
            "portfolios": [],
            "positions": [],
        }
        self.reconciliation = {
            "status": "row_images_match",
            "issues": [],
        }
        self.effects = {
            "status": "no_changes",
            "transaction_groups": [],
        }
        self.valuations = {
            "portfolios": [],
            "quote_coverage": {
                "assets": [
                    {"asset_id": 2, "status": "eligible", "record_id": "a" * 64},
                    {"asset_id": 212, "status": "missing", "record_id": None},
                ],
            },
        }
        self.record = {"price_usd": "10.00000001"}
        self.directory = Path("/unused-test-provenance")

        self.resolver = self.patch(
            "_resolve_portfolios", return_value=self.resolved
        )
        self.reader = self.patch(
            "read_portfolio_audit", return_value=self.audit
        )
        self.builder = self.patch(
            "build_checkpoint_inputs", return_value=self.inputs
        )
        self.reconciler = self.patch(
            "reconcile_audit_snapshot", return_value=self.reconciliation
        )
        self.effect_checker = self.patch(
            "assess_audit_effects", return_value=self.effects
        )
        self.valuator = self.patch(
            "value_provenance_snapshot", return_value=self.valuations
        )
        self.loader = self.patch(
            "load_quote_provenance", return_value=self.record
        )
        self.patch("PROVENANCE_DIRECTORY", new=self.directory)

    def patch(self, name, **kwargs):
        patcher = patch.object(capture, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def test_reads_one_audit_snapshot_with_assets(self):
        capture.capture_portfolio_checkpoint()
        self.reader.assert_called_once_with(
            schema="public", include_assets=True
        )
        self.builder.assert_called_once_with(
            audit_snapshot=self.audit,
            resolved_portfolios=self.resolved,
        )

    def test_accounting_checks_receive_same_snapshot(self):
        capture.capture_portfolio_checkpoint()
        self.reconciler.assert_called_once_with(self.audit)
        self.effect_checker.assert_called_once_with(self.audit)
        self.assertIs(self.reconciler.call_args.args[0], self.audit)
        self.assertIs(self.effect_checker.call_args.args[0], self.audit)

    def test_preserves_actual_sampling_time(self):
        result = capture.capture_portfolio_checkpoint()
        self.assertEqual(result["sampled_at"], self.audit["sampled_at"])
        self.assertFalse(result["historical_return_eligible"])

    def test_provider_selection_remains_explicit(self):
        capture.capture_portfolio_checkpoint()
        arguments = self.valuator.call_args.kwargs
        self.assertEqual(arguments["provider_by_asset"], {
            2: "CoinGecko REST",
            212: "Finnhub REST",
        })
        self.assertIs(arguments["snapshot"], self.inputs)
        self.assertEqual(arguments["directory"], self.directory)
        self.assertEqual(
            arguments["maximum_provider_age"], timedelta(minutes=20)
        )
        self.assertEqual(
            arguments["maximum_capture_age"], timedelta(minutes=2)
        )

    def test_selected_record_is_embedded(self):
        result = capture.capture_portfolio_checkpoint()
        self.loader.assert_called_once_with(
            directory=self.directory,
            record_id="a" * 64,
        )
        self.assertEqual(
            result["selected_quote_records"],
            {"a" * 64: self.record},
        )

    def test_missing_quotes_remain_missing(self):
        result = capture.capture_portfolio_checkpoint()
        rows = result["valuations"]["quote_coverage"]["assets"]
        self.assertEqual(rows[1]["status"], "missing")
        self.assertIsNone(rows[1]["record_id"])
        self.assertEqual(self.loader.call_count, 1)

    def test_ineligible_selected_record_is_retained(self):
        self.valuations["quote_coverage"]["assets"][0]["status"] = "ineligible"
        result = capture.capture_portfolio_checkpoint()
        self.assertIn("a" * 64, result["selected_quote_records"])
        self.assertEqual(
            result["valuations"]["quote_coverage"]["assets"][0]["status"],
            "ineligible",
        )

    def test_ambiguous_quote_does_not_load_arbitrary_record(self):
        self.valuations["quote_coverage"]["assets"][0].update(
            status="ambiguous", record_id=None
        )
        result = capture.capture_portfolio_checkpoint()
        self.loader.assert_not_called()
        self.assertEqual(result["selected_quote_records"], {})

    def test_unresolved_accounting_remains_explicit(self):
        self.reconciliation["status"] = "unresolved"
        self.effects["status"] = "unresolved"
        result = capture.capture_portfolio_checkpoint()
        self.assertEqual(result["row_reconciliation"]["status"], "unresolved")
        self.assertEqual(result["accounting_effects"]["status"], "unresolved")
        self.assertFalse(result["historical_return_eligible"])

    def test_reader_failure_stops_assembly(self):
        self.reader.side_effect = RuntimeError("snapshot unavailable")
        with self.assertRaisesRegex(RuntimeError, "snapshot unavailable"):
            capture.capture_portfolio_checkpoint()
        self.builder.assert_not_called()
        self.valuator.assert_not_called()

    def test_quote_integrity_failure_stops_assembly(self):
        self.loader.side_effect = ValueError("integrity mismatch")
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            capture.capture_portfolio_checkpoint()

    def test_registry_bindings_are_retained(self):
        result = capture.capture_portfolio_checkpoint()
        self.assertEqual(result["resolved_portfolios"], [{
            "portfolio_id": 3,
            **self.resolved[3],
        }])

    def test_inputs_are_not_modified(self):
        original = deepcopy((
            self.resolved, self.audit, self.inputs,
            self.reconciliation, self.effects, self.valuations,
        ))
        capture.capture_portfolio_checkpoint()
        self.assertEqual((
            self.resolved, self.audit, self.inputs,
            self.reconciliation, self.effects, self.valuations,
        ), original)

    def test_no_persistence_or_authority_is_claimed(self):
        result = capture.capture_portfolio_checkpoint()
        for field in (
            "checkpoint_persisted",
            "database_writes",
            "execution_authorized",
            "allocation_authority",
            "live_capital_authority",
            "historical_return_eligible",
        ):
            self.assertIs(result[field], False)
        self.assertTrue(result["limitations"])


if __name__ == "__main__":
    unittest.main()
