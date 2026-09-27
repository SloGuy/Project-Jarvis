"""Tests for prior-context grouping of sampled portfolio returns."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.capital.portfolio_sampled_regime import (
    analyze_sampled_regimes,
    capture_market_context,
)


BASE = datetime(2026, 9, 28, tzinfo=timezone.utc)


def context_record(hour, *, status="success", label="range_bound"):
    observed = BASE + timedelta(hours=hour)
    return {
        "checkpoint": {
            "sampled_at": (observed - timedelta(seconds=1)).isoformat(),
            "finished_at": (observed + timedelta(seconds=1)).isoformat(),
            "market_context": {
                "observed_at": observed.isoformat(),
                "scope": "spy_market_proxy_not_portfolio_regime",
                "report": {
                    "status": status,
                    "combined_regime": label,
                },
            },
        },
    }


def interval(hour, value="0.01"):
    start = BASE + timedelta(hours=hour, minutes=5)
    return {
        "start": start.isoformat(),
        "end": (start + timedelta(hours=1)).isoformat(),
        "return_fraction": value,
    }


def analyze(records, intervals):
    return analyze_sampled_regimes(
        records=records,
        reports_by_portfolio={1: {"returns": intervals}},
    )


class SampledRegimeTests(unittest.TestCase):
    def test_missing_context_excludes_interval(self):
        result = analyze([], [interval(1)])
        self.assertEqual(result["status"], "insufficient_data")
        row = result["portfolios"][0]
        self.assertEqual(row["groups"], [])
        self.assertEqual(
            row["excluded_intervals"][0]["reason"],
            "no_prior_market_context",
        )

    def test_future_context_is_not_used(self):
        result = analyze([context_record(2)], [interval(1)])
        self.assertEqual(
            result["portfolios"][0]["excluded_intervals"][0]["reason"],
            "no_prior_market_context",
        )

    def test_unfinished_capture_is_not_used(self):
        record = context_record(1)
        record["checkpoint"]["finished_at"] = (
            BASE + timedelta(hours=2)
        ).isoformat()
        result = analyze([record], [interval(1)])
        self.assertEqual(
            result["portfolios"][0]["excluded_intervals"][0]["reason"],
            "no_prior_market_context",
        )

    def test_old_context_is_excluded(self):
        result = analyze([context_record(0)], [interval(3)])
        self.assertEqual(
            result["portfolios"][0]["excluded_intervals"][0]["reason"],
            "prior_market_context_too_old",
        )

    def test_unavailable_latest_context_prevents_fallback(self):
        result = analyze(
            [
                context_record(0),
                context_record(1, status="unavailable"),
            ],
            [interval(1)],
        )
        self.assertEqual(
            result["portfolios"][0]["excluded_intervals"][0]["reason"],
            "latest_prior_market_context_unavailable",
        )

    def test_small_group_does_not_publish_mean(self):
        result = analyze([context_record(0)], [interval(0)])
        group = result["portfolios"][0]["groups"][0]
        self.assertEqual(group["observations"], 1)
        self.assertEqual(group["status"], "insufficient_data")
        self.assertIsNone(group["mean_sampled_return_percent"])

    def test_thirty_observations_publish_descriptive_mean(self):
        result = analyze(
            [context_record(hour) for hour in range(30)],
            [interval(hour) for hour in range(30)],
        )
        group = result["portfolios"][0]["groups"][0]
        self.assertEqual(result["status"], "available_indicative")
        self.assertEqual(group["observations"], 30)
        self.assertEqual(group["mean_sampled_return_percent"], "1.00")
        self.assertFalse(result["allocation_authority"])
        self.assertFalse(result["live_capital_authority"])

    def test_regimes_are_counted_separately(self):
        result = analyze(
            [
                context_record(0, label="range_bound"),
                context_record(1, label="trending"),
            ],
            [interval(0), interval(1)],
        )
        groups = result["portfolios"][0]["groups"]
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(row["observations"] == 1 for row in groups))
        self.assertTrue(
            all(row["mean_sampled_return_percent"] is None for row in groups)
        )

    def test_context_before_snapshot_is_rejected(self):
        record = context_record(1)
        record["checkpoint"]["sampled_at"] = (
            BASE + timedelta(hours=2)
        ).isoformat()
        with self.assertRaises(ValueError):
            analyze([record], [interval(2)])

    def test_conflicting_context_is_rejected(self):
        with self.assertRaises(ValueError):
            analyze(
                [
                    context_record(0, label="range_bound"),
                    context_record(0, label="trending"),
                ],
                [interval(0)],
            )

    def test_capture_preserves_market_report(self):
        report = {"status": "success", "combined_regime": "range_bound"}
        with patch(
            "app.capital.market_regime.get_market_regime",
            return_value=report,
        ):
            captured = capture_market_context()
        self.assertEqual(captured["report"], report)
        self.assertEqual(
            captured["scope"],
            "spy_market_proxy_not_portfolio_regime",
        )

    def test_capture_failure_is_explicit(self):
        with patch(
            "app.capital.market_regime.get_market_regime",
            side_effect=RuntimeError("Unavailable"),
        ):
            captured = capture_market_context()
        self.assertEqual(captured["report"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
