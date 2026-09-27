"""Sampled-report assembly and checkpoint discovery tests."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import test_portfolio_sampled_returns as fixtures
from app.capital import portfolio_sampled_report as report


class SampledReportTests(unittest.TestCase):
    def build(self, records=None, ids=None):
        return report.build_sampled_report(
            records=[] if records is None else records,
            portfolio_ids=[1, 2] if ids is None else ids,
            as_of=fixtures.CUTOFF,
        )

    def test_empty_history_reports_insufficient_data(self):
        result = self.build()
        self.assertEqual(result["loaded_checkpoint_count"], 0)
        self.assertIsNone(result["latest_completed_checkpoint"])
        self.assertEqual(result["selected_hourly_checkpoint_count"], 0)
        for row in result["return_reports"]:
            self.assertEqual(row["status"], "insufficient_data")
        self.assertEqual(
            result["analytics"]["risk_contribution"]["status"],
            "insufficient_data",
        )

    def test_current_portfolios_are_not_silently_dropped(self):
        result = self.build([
            fixtures.record(0), fixtures.record(1, "110"),
        ])
        rows = {row["portfolio_id"]: row for row in result["return_reports"]}
        self.assertEqual(set(rows), {1, 2})
        self.assertEqual(rows[1]["return_count"], 1)
        self.assertEqual(rows[2]["return_count"], 0)
        self.assertEqual(
            result["analytics"]["risk_contribution"]["aligned_observations"], 0
        )

    def test_minimum_sample_is_thirty(self):
        self.assertEqual(self.build()["analytics"]["minimum_observations"], 30)

    def test_risk_scenario_is_explicit_and_sums_to_one(self):
        result = self.build(ids=[3, 1, 2])
        weighting = result["risk_weighting"]
        self.assertEqual(
            weighting["methodology"], "equal_paper_account_diagnostic_scenario"
        )
        # Match the precision used by the report and analytics.
        from decimal import localcontext
        with localcontext() as context:
            context.prec = 60
            self.assertEqual(
                sum(
                    (Decimal(row["weight_fraction"]) for row in weighting["weights"]),
                    Decimal("0"),
                ),
                Decimal("1"),
            )
        self.assertEqual(result["portfolio_ids"], [1, 2, 3])

    def test_single_portfolio_weight_is_one(self):
        result = self.build(ids=[1])
        self.assertEqual(
            result["risk_weighting"]["weights"],
            [{"portfolio_id": 1, "weight_fraction": "1"}],
        )

    def test_latest_completed_capture_retains_actual_time(self):
        earlier = fixtures.record(0)
        latest = fixtures.record(1, seconds=1800)
        result = self.build([latest, earlier])
        self.assertEqual(
            result["latest_completed_checkpoint"]["record_id"],
            latest["record_id"],
        )
        self.assertEqual(result["selected_hourly_checkpoint_count"], 1)

    def test_future_capture_is_not_latest_completed(self):
        future = fixtures.record(250)
        earlier = fixtures.record(0)
        result = self.build([future, earlier])
        self.assertEqual(
            result["latest_completed_checkpoint"]["record_id"],
            earlier["record_id"],
        )

    def test_invalid_portfolio_ids_are_rejected(self):
        for ids in ([], [1, 1], [True], [0], ["1"]):
            with self.subTest(ids=ids):
                with self.assertRaises(ValueError):
                    self.build(ids=ids)

    def test_regime_is_not_fabricated(self):
        result = self.build()
        regime = result["regime_attribution"]
        self.assertEqual(regime["status"], "insufficient_data")
        self.assertEqual(
            regime["scope"],
            "sampled_returns_grouped_by_prior_spy_market_context",
        )
        self.assertTrue(regime["portfolios"])
        for portfolio in regime["portfolios"]:
            self.assertEqual(portfolio["groups"], [])

    def test_input_records_are_preserved(self):
        records = [fixtures.record(0), fixtures.record(1)]
        original = deepcopy(records)
        self.build(records)
        self.assertEqual(records, original)

    def test_authority_and_completeness_remain_false(self):
        result = self.build()
        for field in (
            "historical_completeness_verified",
            "database_writes",
            "allocation_authority",
            "live_capital_authority",
        ):
            self.assertIs(result[field], False)


class CheckpointDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_missing_directory_is_empty_without_creation(self):
        missing = self.directory / "missing"
        self.assertEqual(
            report.read_sampled_report_inputs(directory=missing), []
        )
        self.assertFalse(missing.exists())

    def test_every_json_record_is_loaded(self):
        ids = ["b" * 64, "a" * 64]
        for identifier in ids:
            (self.directory / f"{identifier}.json").write_text("placeholder")
        with patch.object(
            report, "load_portfolio_checkpoint", return_value={"test": True}
        ) as loader:
            rows = report.read_sampled_report_inputs(directory=self.directory)
        self.assertEqual([row["record_id"] for row in rows], sorted(ids))
        self.assertEqual(loader.call_count, 2)
        self.assertEqual(rows[0]["checkpoint"], {"test": True})

    def test_unexpected_json_filename_is_rejected(self):
        (self.directory / "unexpected.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "Unexpected"):
            report.read_sampled_report_inputs(directory=self.directory)

    def test_corruption_propagates(self):
        (self.directory / ("a" * 64 + ".json")).write_text("{}")
        with self.assertRaises(ValueError):
            report.read_sampled_report_inputs(directory=self.directory)

    def test_temporary_files_are_not_checkpoints(self):
        (self.directory / ".checkpoint.tmp").write_text("partial")
        self.assertEqual(
            report.read_sampled_report_inputs(directory=self.directory), []
        )

    def test_file_instead_of_directory_is_rejected(self):
        path = self.directory / "file"
        path.write_text("test")
        with self.assertRaises(ValueError):
            report.read_sampled_report_inputs(directory=path)


if __name__ == "__main__":
    unittest.main()
