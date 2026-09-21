"""Tests for segmented daily drawdowns and simultaneous drawdown counts."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from app.capital.portfolio_drawdown_overlap import analyze_drawdown_overlap


BASE = datetime(2026, 8, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)
AS_OF = "2026-09-21T12:00:00+00:00"


def report(values, start_day=0):
    rows = []
    for offset, value in enumerate(values):
        start = BASE + timedelta(days=start_day + offset)
        rows.append({
            "start": start.isoformat(),
            "end": (start + timedelta(days=1)).isoformat(),
            "return_fraction": str(value),
        })
    return {
        "methodology": "completed_utc_daily_returns_v1",
        "as_of": AS_OF,
        "returns": rows,
    }


def analyze(left, right=None, minimum=2):
    reports = {1: left}
    if right is not None:
        reports[2] = right
    return analyze_drawdown_overlap(
        reports_by_portfolio=reports,
        minimum_observations=minimum,
    )


class DrawdownOverlapTests(unittest.TestCase):
    def test_identical_losses_overlap_on_every_day(self):
        result = analyze(
            report(["-0.1", "-0.1"]),
            report(["-0.1", "-0.1"]),
        )
        pair = result["pairs"][0]
        self.assertEqual(pair["status"], "available")
        self.assertEqual(pair["simultaneous_drawdown_observations"], 2)
        self.assertEqual(Decimal(pair["simultaneous_drawdown_percent"]), 100)
        self.assertEqual(
            Decimal(
                result["portfolios"][0]["maximum_segment_drawdown_percent"]
            ),
            19,
        )

    def test_positive_returns_have_no_drawdown(self):
        result = analyze(
            report(["0.1", "0.1"]),
            report(["0.2", "0.1"]),
        )
        self.assertEqual(
            result["pairs"][0]["simultaneous_drawdown_observations"], 0
        )
        self.assertEqual(
            Decimal(result["pairs"][0]["simultaneous_drawdown_percent"]), 0
        )
        self.assertEqual(result["portfolios"][0]["drawdown_observations"], 0)

    def test_exact_peak_recovery_ends_drawdown(self):
        result = analyze(report(["-0.2", "0.25", "0"]))
        summary = result["portfolios"][0]
        self.assertEqual(summary["drawdown_observations"], 1)
        self.assertEqual(
            Decimal(summary["maximum_segment_drawdown_percent"]), 20
        )

    def test_positive_day_can_remain_below_peak(self):
        result = analyze(report(["-0.2", "0.1"]))
        self.assertEqual(
            result["portfolios"][0]["drawdown_observations"], 2
        )

    def test_missing_interval_resets_peak(self):
        history = report(["-0.2", "0", "0.01"])
        del history["returns"][1]
        result = analyze(history)
        summary = result["portfolios"][0]
        self.assertEqual(summary["continuous_segments"], 2)
        self.assertEqual(summary["drawdown_observations"], 1)
        self.assertEqual(
            Decimal(summary["maximum_segment_drawdown_percent"]), 20
        )

    def test_losses_across_gap_are_not_compounded(self):
        history = report(["-0.1", "0", "-0.1"])
        del history["returns"][1]
        summary = analyze(history)["portfolios"][0]
        self.assertEqual(summary["continuous_segments"], 2)
        self.assertEqual(
            Decimal(summary["maximum_segment_drawdown_percent"]), 10
        )

    def test_alignment_uses_exact_intervals(self):
        result = analyze(
            report(["-0.1", "-0.1", "0.5"]),
            report(["-0.1", "0.5", "-0.1"], start_day=1),
        )
        pair = result["pairs"][0]
        self.assertEqual(pair["aligned_observations"], 2)
        self.assertEqual(pair["simultaneous_drawdown_observations"], 1)
        self.assertEqual(
            Decimal(pair["simultaneous_drawdown_percent"]), 50
        )

    def test_pair_alignment_does_not_reset_individual_history(self):
        # The left portfolio remains below its earlier peak during
        # both shared intervals, even though those days have zero returns.
        result = analyze(
            report(["-0.2", "0", "0"]),
            report(["-0.1", "0"], start_day=1),
        )
        pair = result["pairs"][0]
        self.assertEqual(pair["aligned_observations"], 2)
        self.assertEqual(pair["simultaneous_drawdown_observations"], 2)

    def test_insufficient_sample_keeps_counts_but_withholds_percentage(self):
        result = analyze(
            report(["-0.1", "-0.1"]),
            report(["-0.1", "-0.1"]),
            minimum=3,
        )
        pair = result["pairs"][0]
        self.assertEqual(pair["status"], "insufficient_data")
        self.assertEqual(pair["simultaneous_drawdown_observations"], 2)
        self.assertIsNone(pair["simultaneous_drawdown_percent"])

    def test_disjoint_histories_have_no_overlap_estimate(self):
        result = analyze(
            report(["-0.1", "-0.1"]),
            report(["-0.1", "-0.1"], start_day=10),
        )
        pair = result["pairs"][0]
        self.assertEqual(pair["aligned_observations"], 0)
        self.assertIsNone(pair["simultaneous_drawdown_percent"])
        self.assertIsNone(pair["first_interval_start"])

    def test_empty_history_has_no_drawdown_estimate(self):
        summary = analyze(report([]))["portfolios"][0]
        self.assertEqual(summary["observation_count"], 0)
        self.assertEqual(summary["continuous_segments"], 0)
        self.assertIsNone(summary["maximum_segment_drawdown_percent"])

    def test_invalid_minimum_is_rejected(self):
        for value in (True, 0, 1, "2", 2.0):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    analyze(report([]), minimum=value)

    def test_mismatched_as_of_times_are_rejected(self):
        left = report(["0.01", "0.02"])
        right = report(["0.01", "0.02"])
        right["as_of"] = "2026-09-20T12:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "same as-of"):
            analyze(left, right)

    def test_duplicate_intervals_are_rejected(self):
        history = report(["0.01", "0.02"])
        history["returns"].append(deepcopy(history["returns"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            analyze(history)

    def test_invalid_returns_are_rejected(self):
        for value in ("-1", "NaN", "Infinity", True):
            with self.subTest(value=value):
                history = report(["0.01"])
                history["returns"][0]["return_fraction"] = value
                with self.assertRaises(ValueError):
                    analyze(history)

    def test_inputs_are_preserved_and_order_is_deterministic(self):
        history = report(["-0.2", "0.1", "0.2"])
        original = deepcopy(history)
        first = analyze(history)
        self.assertEqual(history, original)
        history["returns"].reverse()
        self.assertEqual(first, analyze(history))

    def test_empty_collection_and_authority_boundaries(self):
        result = analyze_drawdown_overlap(reports_by_portfolio={})
        self.assertEqual(result["portfolios"], [])
        self.assertEqual(result["pairs"], [])
        self.assertIsNone(result["as_of"])
        for field in (
            "database_writes",
            "allocation_authority",
            "live_capital_authority",
        ):
            self.assertFalse(result[field])


if __name__ == "__main__":
    unittest.main()
