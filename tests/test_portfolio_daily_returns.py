"""Daily-return interval tests; no database access."""
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import unittest

from app.capital.portfolio_daily_returns import build_daily_returns


class DailyReturnTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.cutoff = self.start + timedelta(days=5)

    def point(self, day, value, status="usable"):
        at = datetime.combine(
            (self.start + timedelta(days=day)).date(),
            time.max,
            tzinfo=timezone.utc,
        )
        return {
            "measured_at": at.isoformat(),
            "total_value_usd": value,
            "valuation_status": status,
        }

    def build(self, points, flows=()):
        return build_daily_returns(
            points=points,
            external_flow_times=flows,
            as_of=self.cutoff,
        )

    def test_complete_daily_returns(self):
        result = self.build([
            self.point(0, "100"),
            self.point(1, "110"),
            self.point(2, "99"),
        ])
        self.assertEqual(result["return_count"], 2)
        self.assertEqual(
            [Decimal(row["return_fraction"]) for row in result["returns"]],
            [Decimal("0.1"), Decimal("-0.1")],
        )
        self.assertFalse(result["database_writes"])
        self.assertFalse(result["allocation_authority"])
        self.assertFalse(result["live_capital_authority"])
        self.assertFalse(result["historical_availability_verified"])

    def test_partial_day_points_are_excluded(self):
        partial = {
            "measured_at": (self.start + timedelta(hours=12)).isoformat(),
            "total_value_usd": "100",
            "valuation_status": "usable",
        }
        result = self.build([partial, self.point(0, "101"), self.point(1, "102")])
        self.assertEqual(result["return_count"], 1)
        self.assertEqual(
            result["excluded_points"][0]["reason"], "not_utc_daily_boundary"
        )

    def test_current_or_future_measurements_are_excluded(self):
        result = self.build([
            self.point(0, "100"),
            self.point(1, "101"),
            {
                "measured_at": self.cutoff.isoformat(),
                "total_value_usd": "999",
                "valuation_status": "usable",
            },
            self.point(6, "999"),
        ])
        self.assertEqual(result["return_count"], 1)
        self.assertEqual(len(result["excluded_points"]), 2)
        self.assertTrue(all(
            row["reason"] == "not_completed"
            for row in result["excluded_points"]
        ))

    def test_missing_day_is_not_bridged(self):
        result = self.build([
            self.point(0, "100"), self.point(2, "110"), self.point(3, "121")
        ])
        self.assertEqual(result["return_count"], 1)
        self.assertEqual(
            result["excluded_intervals"][0]["reasons"],
            ["missing_daily_boundary"],
        )

    def test_unverified_point_invalidates_both_adjacent_intervals(self):
        result = self.build([
            self.point(0, "100"),
            self.point(1, "110", "stale"),
            self.point(2, "121"),
        ])
        self.assertEqual(result["return_count"], 0)
        self.assertEqual(len(result["excluded_intervals"]), 2)
        for row in result["excluded_intervals"]:
            self.assertIn("unverified_valuation", row["reasons"])

    def test_missing_quality_label_is_not_assumed_usable(self):
        point = self.point(0, "100")
        del point["valuation_status"]
        result = self.build([point, self.point(1, "110")])
        self.assertEqual(result["return_count"], 0)

    def test_external_flow_interval_is_excluded(self):
        result = self.build(
            [self.point(0, "100"), self.point(1, "200"), self.point(2, "220")],
            flows=[(self.start + timedelta(days=1, hours=12)).isoformat()],
        )
        self.assertEqual(result["return_count"], 1)
        self.assertEqual(
            Decimal(result["returns"][0]["return_fraction"]), Decimal("0.1")
        )
        self.assertIn(
            "external_cash_flow", result["excluded_intervals"][0]["reasons"]
        )

    def test_flow_boundary_convention(self):
        first = self.point(0, "100")
        second = self.point(1, "110")
        at_start = self.build([first, second], flows=[first["measured_at"]])
        at_end = self.build([first, second], flows=[second["measured_at"]])
        self.assertEqual(at_start["return_count"], 1)
        self.assertEqual(at_end["return_count"], 0)

    def test_invalid_equity_is_excluded(self):
        for value in ("NaN", "Infinity", "-1", "0", None, True, "bad"):
            with self.subTest(value=value):
                result = self.build([self.point(0, value), self.point(1, "110")])
                self.assertEqual(result["return_count"], 0)
                self.assertIn(
                    "invalid_or_nonpositive_equity",
                    result["excluded_intervals"][0]["reasons"],
                )

    def test_duplicate_timestamps_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.build([self.point(0, "100"), self.point(0, "101")])

    def test_naive_timestamps_are_rejected(self):
        point = self.point(0, "100")
        point["measured_at"] = "2026-09-01T23:59:59.999999"
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.build([point])

    def test_cash_flow_inventory_must_be_explicit(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            self.build([], flows=None)

    def test_sorting_preserves_inputs_and_results(self):
        points = [self.point(2, "121"), self.point(0, "100"), self.point(1, "110")]
        original = deepcopy(points)
        result = self.build(points)
        self.assertEqual(points, original)
        self.assertEqual(result, self.build(list(reversed(points))))

    def test_offset_timestamps_are_normalized_to_utc(self):
        first = self.point(0, "100")
        first["measured_at"] = "2026-09-01T19:59:59.999999-04:00"
        result = self.build([first, self.point(1, "110")])
        self.assertEqual(result["return_count"], 1)
        self.assertEqual(
            result["returns"][0]["start"], "2026-09-01T23:59:59.999999+00:00"
        )


if __name__ == "__main__":
    unittest.main()
