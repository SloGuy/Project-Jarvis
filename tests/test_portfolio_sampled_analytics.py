"""Numerical and evidence-boundary tests for sampled analytics."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from app.capital.portfolio_sampled_analytics import analyze_sampled_returns
from app.capital.portfolio_sampled_returns import METHOD


BASE = datetime(2026, 9, 28, tzinfo=timezone.utc)
CUTOFF = BASE + timedelta(days=10)


def report(values):
    return {
        "methodology": METHOD,
        "as_of": CUTOFF.isoformat(),
        "returns": [
            {
                "start": (BASE + timedelta(hours=i)).isoformat(),
                "end": (BASE + timedelta(hours=i + 1)).isoformat(),
                "return_fraction": str(value),
            }
            for i, value in enumerate(values)
        ],
    }


class SampledAnalyticsTests(unittest.TestCase):
    def analyze(self, left, right, **kwargs):
        return analyze_sampled_returns(
            reports_by_portfolio={1: report(left), 2: report(right)},
            minimum_observations=2,
            **kwargs,
        )

    def test_identical_series_have_positive_unit_correlation(self):
        result = self.analyze(["0.01", "-0.02", "0.03"], ["0.01", "-0.02", "0.03"])
        pair = result["correlation"]["pairs"][0]
        self.assertEqual(pair["status"], "available_indicative")
        self.assertEqual(Decimal(pair["correlation"]), Decimal("1"))

    def test_opposite_series_have_negative_unit_correlation(self):
        result = self.analyze(["0.01", "-0.02"], ["-0.01", "0.02"])
        self.assertEqual(
            Decimal(result["correlation"]["pairs"][0]["correlation"]),
            Decimal("-1"),
        )

    def test_constant_returns_have_undefined_correlation(self):
        result = self.analyze(["0", "0"], ["0.01", "-0.01"])
        pair = result["correlation"]["pairs"][0]
        self.assertEqual(pair["status"], "undefined_constant_returns")
        self.assertIsNone(pair["correlation"])

    def test_default_requires_thirty_observations(self):
        values = ["0.01", "-0.01"] * 14 + ["0.02"]
        result = analyze_sampled_returns(
            reports_by_portfolio={1: report(values), 2: report(values)}
        )
        self.assertEqual(
            result["correlation"]["pairs"][0]["status"], "insufficient_data"
        )

    def test_thirty_aligned_observations_enable_descriptive_result(self):
        values = ["0.01", "-0.01"] * 15
        result = analyze_sampled_returns(
            reports_by_portfolio={1: report(values), 2: report(values)}
        )
        self.assertEqual(
            result["correlation"]["pairs"][0]["status"], "available_indicative"
        )

    def test_only_exact_shared_intervals_are_compared(self):
        left = report(["0.01", "-0.01", "0.02"])
        right = report(["0.01", "-0.01", "0.02"])
        right["returns"].pop(1)
        result = analyze_sampled_returns(
            reports_by_portfolio={1: left, 2: right},
            minimum_observations=2,
        )
        self.assertEqual(
            result["correlation"]["pairs"][0]["aligned_observations"], 2
        )

    def test_drawdown_overlap_percentage(self):
        result = self.analyze(["-0.1", "0.2"], ["-0.1", "0.2"])
        overlap = result["drawdown_overlap"]["pairs"][0]
        self.assertEqual(
            Decimal(overlap["simultaneous_drawdown_percent"]), Decimal("50")
        )

    def test_drawdown_resets_after_gap(self):
        left = report(["-0.5", "0", "0.1"])
        right = report(["-0.5", "0", "0.1"])
        left["returns"].pop(1)
        right["returns"].pop(1)
        result = analyze_sampled_returns(
            reports_by_portfolio={1: left, 2: right},
            minimum_observations=2,
        )
        self.assertEqual(
            Decimal(
                result["drawdown_overlap"]["pairs"][0][
                    "simultaneous_drawdown_percent"
                ]
            ),
            Decimal("50"),
        )

    def test_risk_uses_sample_variance_and_explicit_weights(self):
        result = self.analyze(
            ["-0.01", "0.01"],
            ["-0.01", "0.01"],
            weights_by_portfolio={1: "0.5", 2: "0.5"},
        )
        risk = result["risk_contribution"]
        self.assertEqual(
            Decimal(risk["sampled_interval_variance"]), Decimal("0.0002")
        )
        for row in risk["portfolios"]:
            self.assertEqual(
                Decimal(row["risk_contribution_percent"]), Decimal("50")
            )

    def test_negative_risk_contribution_is_retained(self):
        result = self.analyze(
            ["-0.02", "0.02"],
            ["0.01", "-0.01"],
            weights_by_portfolio={1: "0.75", 2: "0.25"},
        )
        rows = result["risk_contribution"]["portfolios"]
        self.assertLess(Decimal(rows[1]["variance_contribution"]), 0)
        total = sum(
            (Decimal(row["risk_contribution_percent"]) for row in rows),
            Decimal("0"),
        )
        self.assertAlmostEqual(total, Decimal("100"), places=25)

    def test_zero_variance_is_explicit(self):
        result = self.analyze(
            ["0.01", "-0.01"], ["-0.01", "0.01"],
            weights_by_portfolio={1: "0.5", 2: "0.5"},
        )
        self.assertEqual(
            result["risk_contribution"]["status"], "undefined_zero_variance"
        )

    def test_risk_uses_common_sample_including_zero_weight_portfolio(self):
        left = report(["0.01", "-0.01", "0.02"])
        right = report(["0.01", "-0.01", "0.02"])
        right["returns"] = right["returns"][:1]
        result = analyze_sampled_returns(
            reports_by_portfolio={1: left, 2: right},
            weights_by_portfolio={1: "1", 2: "0"},
            minimum_observations=2,
        )
        self.assertEqual(result["risk_contribution"]["aligned_observations"], 1)
        self.assertEqual(
            result["risk_contribution"]["status"], "insufficient_data"
        )

    def test_weights_are_not_invented(self):
        result = self.analyze(["0.01", "-0.01"], ["0.01", "-0.01"])
        self.assertEqual(
            result["risk_contribution"]["status"], "weights_not_supplied"
        )

    def test_invalid_weights_are_rejected(self):
        for weights in (
            {1: "1"},
            {1: "-1", 2: "2"},
            {1: "0.2", 2: "0.2"},
            {1: True, 2: "0"},
            {1: "NaN", 2: "0"},
        ):
            with self.subTest(weights=weights):
                with self.assertRaises(ValueError):
                    self.analyze(
                        ["0.01", "-0.01"], ["0.01", "-0.01"],
                        weights_by_portfolio=weights,
                    )

    def test_daily_methodology_is_rejected(self):
        data = report(["0", "0"])
        data["methodology"] = "completed_utc_daily_returns_v1"
        with self.assertRaises(ValueError):
            analyze_sampled_returns(reports_by_portfolio={1: data})

    def test_different_cutoffs_are_rejected(self):
        left, right = report(["0", "0"]), report(["0", "0"])
        right["as_of"] = (CUTOFF + timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError):
            analyze_sampled_returns(reports_by_portfolio={1: left, 2: right})

    def test_duplicate_or_overlapping_intervals_are_rejected(self):
        for duplicate in (True, False):
            with self.subTest(duplicate=duplicate):
                data = report(["0.01", "0.02"])
                if duplicate:
                    data["returns"][1] = deepcopy(data["returns"][0])
                else:
                    data["returns"][1].update(
                        start=(BASE + timedelta(minutes=30)).isoformat(),
                        end=(BASE + timedelta(minutes=90)).isoformat(),
                    )
                with self.assertRaises(ValueError):
                    analyze_sampled_returns(reports_by_portfolio={1: data})

    def test_invalid_duration_and_unfinished_intervals_are_rejected(self):
        for end in (
            BASE + timedelta(minutes=30),
            BASE + timedelta(hours=2),
        ):
            with self.subTest(end=end):
                data = report(["0"])
                data["returns"][0]["end"] = end.isoformat()
                with self.assertRaises(ValueError):
                    analyze_sampled_returns(reports_by_portfolio={1: data})
        data = report(["0"])
        data["as_of"] = data["returns"][0]["end"]
        with self.assertRaises(ValueError):
            analyze_sampled_returns(reports_by_portfolio={1: data})

    def test_invalid_returns_are_rejected(self):
        for value in ("-1", "-2", "NaN", "Infinity", True):
            with self.subTest(value=value):
                data = report(["0"])
                data["returns"][0]["return_fraction"] = value
                with self.assertRaises(ValueError):
                    analyze_sampled_returns(reports_by_portfolio={1: data})

    def test_empty_reports_are_supported(self):
        result = analyze_sampled_returns(reports_by_portfolio={})
        self.assertEqual(result["correlation"]["pairs"], [])
        self.assertEqual(result["drawdown_overlap"]["pairs"], [])

    def test_inputs_and_authority_are_preserved(self):
        reports = {1: report(["0.01", "-0.01"]), 2: report(["0", "0.02"])}
        original = deepcopy(reports)
        result = analyze_sampled_returns(
            reports_by_portfolio=reports, minimum_observations=2
        )
        self.assertEqual(reports, original)
        for field in (
            "database_writes", "allocation_authority", "live_capital_authority"
        ):
            self.assertIs(result[field], False)


if __name__ == "__main__":
    unittest.main()
