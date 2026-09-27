"""Saved-report freshness and binding tests using temporary files."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.capital import portfolio_sampled_status as status


NOW = datetime(2026, 9, 28, 1, tzinfo=timezone.utc)


class SampledStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "report.json"
        patcher = patch.object(status, "REPORT_FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.resolved = {
            1: {
                "portfolio_name": "Test Paper",
                "strategy_name": "test",
                "experiment_id": "test-1",
            },
        }
        self.report = {
            "schema_version": 1,
            "methodology": "portfolio_sampled_report_v1",
            "as_of": (NOW - timedelta(minutes=30)).isoformat(),
            "generated_at": (NOW - timedelta(minutes=29)).isoformat(),
            "portfolio_ids": [1],
            "portfolio_bindings": [{
                "portfolio_id": 1, **self.resolved[1],
            }],
            "analytics": {
                "methodology": "aligned_hourly_sampled_analytics_v1",
                "risk_contribution": {"status": "insufficient_data"},
            },
            "historical_completeness_verified": False,
            "database_writes": False,
            "allocation_authority": False,
            "live_capital_authority": False,
        }

    def write(self):
        self.path.write_text(json.dumps(self.report), encoding="utf-8")

    def read(self):
        return status.get_sampled_status(
            resolved_portfolios=self.resolved, now=NOW
        )

    def test_current_insufficient_data_report_is_returned(self):
        self.write()
        result = self.read()
        self.assertEqual(result["status"], "current")
        self.assertEqual(result["report"], self.report)

    def test_missing_report_is_explicit(self):
        result = self.read()
        self.assertEqual(result["reason"], "report_not_published")
        self.assertIsNone(result["report"])

    def test_stale_report_does_not_expose_current_metrics(self):
        self.report["as_of"] = (NOW - timedelta(minutes=91)).isoformat()
        self.write()
        result = self.read()
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["reason"], "report_refresh_overdue")
        self.assertIsNone(result["report"])
        self.assertEqual(result["report_as_of"], self.report["as_of"])

    def test_ninety_minute_boundary_is_current(self):
        self.report["as_of"] = (NOW - timedelta(minutes=90)).isoformat()
        self.write()
        self.assertEqual(self.read()["status"], "current")

    def test_future_or_inverted_timestamps_are_invalid(self):
        cases = [
            {"as_of": (NOW + timedelta(seconds=1)).isoformat()},
            {"generated_at": (NOW + timedelta(seconds=1)).isoformat()},
            {"generated_at": (NOW - timedelta(hours=1)).isoformat()},
            {"as_of": "2026-09-28T00:30:00"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                original = dict(self.report)
                self.report.update(changes)
                self.write()
                self.assertEqual(
                    self.read()["reason"], "report_unreadable_or_invalid"
                )
                self.report = original

    def test_changed_portfolio_binding_is_unavailable(self):
        self.write()
        self.resolved[1]["strategy_name"] = "different"
        self.assertEqual(self.read()["reason"], "portfolio_bindings_changed")

    def test_changed_portfolio_set_is_unavailable(self):
        self.write()
        self.resolved[2] = dict(self.resolved[1])
        self.assertEqual(self.read()["reason"], "portfolio_bindings_changed")

    def test_duplicate_json_keys_are_rejected(self):
        self.path.write_text('{"schema_version":1,"schema_version":1}')
        self.assertEqual(self.read()["reason"], "report_unreadable_or_invalid")

    def test_malformed_and_nonobject_reports_are_rejected(self):
        for raw in ("{", "[]", "null", '{"bad":NaN}'):
            with self.subTest(raw=raw):
                self.path.write_text(raw)
                self.assertEqual(
                    self.read()["reason"], "report_unreadable_or_invalid"
                )

    def test_unknown_versions_and_methods_are_rejected(self):
        for field, value in (
            ("schema_version", True),
            ("schema_version", 2),
            ("methodology", "other"),
        ):
            with self.subTest(field=field):
                original = self.report[field]
                self.report[field] = value
                self.write()
                self.assertEqual(
                    self.read()["reason"], "report_unreadable_or_invalid"
                )
                self.report[field] = original

    def test_unknown_analytics_method_is_rejected(self):
        self.report["analytics"]["methodology"] = "daily"
        self.write()
        self.assertEqual(self.read()["reason"], "report_unreadable_or_invalid")

    def test_authority_claims_are_rejected(self):
        for field in (
            "historical_completeness_verified",
            "database_writes",
            "allocation_authority",
            "live_capital_authority",
        ):
            with self.subTest(field=field):
                self.report[field] = True
                self.write()
                self.assertIsNone(self.read()["report"])
                self.report[field] = False

    def test_missing_required_field_is_rejected(self):
        del self.report["analytics"]
        self.write()
        self.assertEqual(self.read()["reason"], "report_unreadable_or_invalid")

    def test_file_is_not_modified_and_authority_remains_disabled(self):
        self.write()
        before = self.path.read_bytes()
        result = self.read()
        self.assertEqual(self.path.read_bytes(), before)
        for field in (
            "database_writes",
            "execution_authorized",
            "allocation_authority",
            "live_capital_authority",
        ):
            self.assertIs(result[field], False)


if __name__ == "__main__":
    unittest.main()
