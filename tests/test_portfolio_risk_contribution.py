"""Tests for common-sample historical variance contributions."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import unittest

from app.capital.portfolio_risk_contribution import analyze_risk_contribution


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


def analyze(reports, weights, minimum=3):
    return analyze_risk_contribution(
        reports_by_portfolio=reports,
        weights_by_portfolio=weights,
        minimum_observations=minimum,
    )


class RiskContributionTests(unittest.TestCase):
    def test_identical_series_contributions_follow_weights(self):
        history = report(["-0.01", "0", "0.01"])
        result = analyze(
            {1: history, 2: deepcopy(history)},
            {1: "0.25", 2: "0.75"},
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(Decimal(result["daily_variance"]), Decimal("0.0001"))
        self.assertEqual(Decimal(result["daily_volatility_percent"]), 1)
        self.assertEqual(
            [
                Decimal(row["risk_contribution_percent"])
                for row in result["portfolios"]
            ],
            [Decimal("25"), Decimal("75")],
        )

    def test_cash_portfolio_has_zero_variance_contribution(self):
        result = analyze(
            {
                1: report(["-0.01", "0", "0.01"]),
                2: report(["0", "0", "0"]),
            },
            {1: "0.5", 2: "0.5"},
        )
        self.assertEqual(
            Decimal(result["daily_variance"]), Decimal("0.000025")
        )
        self.assertEqual(
            Decimal(result["portfolios"][0]["risk_contribution_percent"]), 100
        )
        self.assertEqual(
            Decimal(result["portfolios"][1]["risk_contribution_percent"]), 0
        )

    def test_exact_offset_has_undefined_percentage_contributions(self):
        result = analyze(
            {
                1: report(["-0.01", "0", "0.01"]),
                2: report(["0.01", "0", "-0.01"]),
            },
            {1: "0.5", 2: "0.5"},
        )
        self.assertEqual(result["status"], "undefined_zero_variance")
        self.assertEqual(Decimal(result["daily_variance"]), 0)
        for row in result["portfolios"]:
            self.assertIsNone(row["risk_contribution_percent"])

    def test_negative_contributions_are_preserved(self):
        result = analyze(
            {
                1: report(["-0.01", "0", "0.01"]),
                2: report(["0.01", "0", "-0.01"]),
            },
            {1: "0.75", 2: "0.25"},
        )
        self.assertEqual(
            [
                Decimal(row["risk_contribution_percent"])
                for row in result["portfolios"]
            ],
            [Decimal("150"), Decimal("-50")],
        )

    def test_contributions_reconcile_to_total_variance(self):
        result = analyze(
            {
                1: report(["0.01", "-0.02", "0.04", "0.005"]),
                2: report(["-0.01", "0.03", "0.02", "-0.005"]),
                3: report(["0", "0.01", "-0.01", "0.02"]),
            },
            {1: "0.2", 2: "0.3", 3: "0.5"},
        )
        with localcontext() as context:
            context.prec = 60
            contributions = sum(
                (
                    Decimal(row["variance_contribution"])
                    for row in result["portfolios"]
                ),
                Decimal("0"),
            )
            percentages = sum(
                (
                    Decimal(row["risk_contribution_percent"])
                    for row in result["portfolios"]
                ),
                Decimal("0"),
            )
            self.assertLess(
                abs(contributions - Decimal(result["daily_variance"])),
                Decimal("1e-50"),
            )
            self.assertLess(abs(percentages - 100), Decimal("1e-50"))

    def test_alignment_requires_dates_shared_by_every_portfolio(self):
        result = analyze(
            {
                1: report(["0.01", "0.02", "0.03", "0.04"]),
                2: report(["0.01", "0.02", "0.03", "0.04"], start_day=1),
                3: report(["0.01", "0.02", "0.03", "0.04"], start_day=2),
            },
            {1: "0.2", 2: "0.3", 3: "0.5"},
        )
        self.assertEqual(result["aligned_observations"], 2)
        self.assertEqual(result["status"], "insufficient_data")
        self.assertIsNone(result["daily_variance"])

    def test_zero_weight_portfolio_still_participates_in_alignment(self):
        result = analyze(
            {
                1: report(["0.01", "0.02", "0.03"]),
                2: report([]),
            },
            {1: "1", 2: "0"},
        )
        self.assertEqual(result["aligned_observations"], 0)
        self.assertEqual(result["status"], "insufficient_data")

    def test_single_portfolio_contributes_all_variance(self):
        result = analyze(
            {1: report(["-0.01", "0", "0.01"])},
            {1: "1"},
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(
            Decimal(result["portfolios"][0]["risk_contribution_percent"]), 100
        )

    def test_default_minimum_is_thirty(self):
        result = analyze_risk_contribution(
            reports_by_portfolio={1: report(["0.01", "0.02", "0.03"])},
            weights_by_portfolio={1: "1"},
        )
        self.assertEqual(result["minimum_observations"], 30)
        self.assertEqual(result["status"], "insufficient_data")

    def test_weights_must_sum_to_one(self):
        reports = {1: report([]), 2: report([])}
        for weights in ({1: "0.4", 2: "0.4"}, {1: "1", 2: "1"}):
            with self.subTest(weights=weights):
                with self.assertRaisesRegex(ValueError, "sum to one"):
                    analyze(reports, weights)

    def test_invalid_weights_are_rejected(self):
        for value in (True, None, "-1", "NaN", "Infinity", "bad"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    analyze({1: report([])}, {1: value})

    def test_report_and_weight_universes_must_match(self):
        with self.assertRaisesRegex(ValueError, "identical portfolios"):
            analyze({1: report([]), 2: report([])}, {1: "1"})

    def test_invalid_ids_are_rejected_in_both_mappings(self):
        for identifier in (True, 0, -1, "1"):
            with self.subTest(identifier=identifier):
                with self.assertRaises(ValueError):
                    analyze(
                        {identifier: report([])},
                        {identifier: "1"},
                    )

        # Python considers True and 1 equal as keys; validate both mappings.
        with self.assertRaises(ValueError):
            analyze({1: report([])}, {True: "1"})

    def test_invalid_minimum_is_rejected(self):
        for minimum in (True, 0, 1, "3", 3.0):
            with self.subTest(minimum=minimum):
                with self.assertRaises(ValueError):
                    analyze({1: report([])}, {1: "1"}, minimum=minimum)

    def test_as_of_times_must_match(self):
        left = report(["0.01", "0.02", "0.03"])
        right = deepcopy(left)
        right["as_of"] = "2026-09-20T12:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "same as-of"):
            analyze({1: left, 2: right}, {1: "0.5", 2: "0.5"})

    def test_empty_universe_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "At least one"):
            analyze({}, {})

    def test_inputs_are_preserved_and_order_is_deterministic(self):
        reports = {
            2: report(["0.02", "-0.01", "0.03"]),
            1: report(["-0.01", "0", "0.01"]),
        }
        weights = {2: "0.6", 1: "0.4"}
        original = deepcopy((reports, weights))
        result = analyze(reports, weights)
        self.assertEqual((reports, weights), original)

        reordered = {}
        for key in sorted(reports):
            reordered[key] = deepcopy(reports[key])
            reordered[key]["returns"].reverse()
        self.assertEqual(result, analyze(reordered, weights))

    def test_no_execution_or_allocation_authority(self):
        result = analyze(
            {1: report(["-0.01", "0", "0.01"])},
            {1: "1"},
        )
        for field in (
            "database_writes",
            "allocation_authority",
            "live_capital_authority",
        ):
            self.assertFalse(result[field])


if __name__ == "__main__":
    unittest.main()
