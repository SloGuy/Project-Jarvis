"""Tests for aligned daily return correlation."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from app.capital.portfolio_correlation import analyze_correlations


BASE = datetime(2026, 8, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)
AS_OF = "2026-09-21T12:00:00+00:00"


def report(values, start_day=0):
    rows = []
    for offset, value in enumerate(values):
        start = BASE + timedelta(days=start_day + offset)
        end = start + timedelta(days=1)
        rows.append({
            "start": start.isoformat(),
            "end": end.isoformat(),
            "return_fraction": str(value),
        })
    return {
        "methodology": "completed_utc_daily_returns_v1",
        "as_of": AS_OF,
        "returns": rows,
    }


def analyze(left, right, minimum=3):
    return analyze_correlations(
        reports_by_portfolio={1: left, 2: right},
        minimum_observations=minimum,
    )["pairs"][0]


class CorrelationTests(unittest.TestCase):
    def test_identical_varying_returns_have_positive_one_correlation(self):
        values = ["0.01", "-0.02", "0.03"]
        pair = analyze(report(values), report(values))
        self.assertEqual(pair["status"], "available")
        self.assertEqual(Decimal(pair["correlation"]), 1)

    def test_opposite_returns_have_negative_one_correlation(self):
        pair = analyze(
            report(["0.01", "-0.02", "0.03"]),
            report(["-0.01", "0.02", "-0.03"]),
        )
        self.assertEqual(Decimal(pair["correlation"]), -1)

    def test_known_zero_correlation(self):
        pair = analyze(
            report(["-0.01", "0", "0.01"]),
            report(["0.01", "-0.02", "0.01"]),
        )
        self.assertEqual(pair["status"], "available")
        self.assertEqual(Decimal(pair["correlation"]), 0)

    def test_alignment_uses_dates_not_list_positions(self):
        left = report(["0.9", "0.01", "-0.02", "0.03"])
        right = report(["0.01", "-0.02", "0.03", "-0.8"], start_day=1)
        pair = analyze(left, right)
        self.assertEqual(pair["aligned_observations"], 3)
        self.assertEqual(pair["left_unmatched_observations"], 1)
        self.assertEqual(pair["right_unmatched_observations"], 1)
        self.assertEqual(Decimal(pair["correlation"]), 1)

    def test_missing_intervals_are_not_filled(self):
        left = report(["0.01", "0.02", "0.03"])
        right = deepcopy(left)
        del right["returns"][1]
        pair = analyze(left, right)
        self.assertEqual(pair["status"], "insufficient_data")
        self.assertEqual(pair["aligned_observations"], 2)
        self.assertIsNone(pair["correlation"])

    def test_disjoint_histories_have_no_correlation(self):
        pair = analyze(
            report(["0.01", "0.02", "0.03"]),
            report(["0.01", "0.02", "0.03"], start_day=10),
        )
        self.assertEqual(pair["aligned_observations"], 0)
        self.assertIsNone(pair["correlation"])
        self.assertIsNone(pair["first_interval_start"])
        self.assertIsNone(pair["last_interval_end"])

    def test_constant_returns_are_undefined_not_zero_correlation(self):
        for constant in ("0", "0.01"):
            with self.subTest(constant=constant):
                pair = analyze(
                    report([constant] * 3),
                    report(["0.01", "0.02", "0.03"]),
                )
                self.assertEqual(pair["status"], "undefined_constant_returns")
                self.assertIsNone(pair["correlation"])

    def test_default_minimum_requires_thirty_observations(self):
        result = analyze_correlations(reports_by_portfolio={
            1: report(["0.01", "0.02", "0.03"]),
            2: report(["0.01", "0.02", "0.03"]),
        })
        self.assertEqual(result["minimum_observations"], 30)
        self.assertEqual(result["pairs"][0]["status"], "insufficient_data")

    def test_invalid_minimum_is_rejected(self):
        for minimum in (True, 0, 1, 3.0, "3"):
            with self.subTest(minimum=minimum):
                with self.assertRaises(ValueError):
                    analyze_correlations(
                        reports_by_portfolio={},
                        minimum_observations=minimum,
                    )

    def test_duplicate_intervals_are_rejected(self):
        left = report(["0.01", "0.02", "0.03"])
        left["returns"].append(deepcopy(left["returns"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            analyze(left, report(["0.01", "0.02", "0.03"]))

    def test_partial_or_multiday_intervals_are_rejected(self):
        for end in (
            "2026-08-02T12:00:00+00:00",
            "2026-08-03T23:59:59.999999+00:00",
        ):
            with self.subTest(end=end):
                left = report(["0.01"])
                left["returns"][0]["end"] = end
                with self.assertRaisesRegex(ValueError, "complete UTC"):
                    analyze(left, report(["0.01"]))

    def test_uncompleted_interval_is_rejected(self):
        left = report(["0.01"])
        left["as_of"] = left["returns"][0]["end"]
        with self.assertRaisesRegex(ValueError, "not completed"):
            analyze(left, report(["0.01"]))

    def test_different_as_of_times_are_rejected(self):
        left = report(["0.01"])
        left["as_of"] = "2026-09-20T12:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "same as-of"):
            analyze(left, report(["0.01"]))

    def test_invalid_returns_are_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity", "-1", "-2", True, None):
            with self.subTest(value=value):
                left = report(["0.01"])
                left["returns"][0]["return_fraction"] = value
                with self.assertRaises(ValueError):
                    analyze(left, report(["0.01"]))

    def test_wrong_methodology_and_invalid_ids_are_rejected(self):
        left = report(["0.01"])
        left["methodology"] = "indicative_returns"
        with self.assertRaisesRegex(ValueError, "methodology"):
            analyze(left, report(["0.01"]))

        for identifier in (True, 0, -1, "1"):
            with self.subTest(identifier=identifier):
                with self.assertRaises(ValueError):
                    analyze_correlations(
                        reports_by_portfolio={identifier: report([])}
                    )

    def test_all_pairs_are_reported_in_stable_order(self):
        result = analyze_correlations(
            reports_by_portfolio={
                3: report(["0.03", "0.02", "0.01"]),
                1: report(["0.01", "0.02", "0.03"]),
                2: report(["0", "0", "0"]),
            },
            minimum_observations=3,
        )
        self.assertEqual(
            [
                (row["left_portfolio_id"], row["right_portfolio_id"])
                for row in result["pairs"]
            ],
            [(1, 2), (1, 3), (2, 3)],
        )

    def test_inputs_are_preserved_and_order_does_not_change_result(self):
        left = report(["0.01", "-0.02", "0.03"])
        right = deepcopy(left)
        original = deepcopy((left, right))
        first = analyze(left, right)
        self.assertEqual((left, right), original)
        left["returns"].reverse()
        self.assertEqual(first, analyze(left, right))

    def test_empty_input_and_authority_boundaries(self):
        result = analyze_correlations(reports_by_portfolio={})
        self.assertEqual(result["pairs"], [])
        self.assertEqual(result["portfolio_count"], 0)
        self.assertIsNone(result["as_of"])
        for field in (
            "database_writes",
            "allocation_authority",
            "live_capital_authority",
        ):
            self.assertFalse(result[field])


if __name__ == "__main__":
    unittest.main()
