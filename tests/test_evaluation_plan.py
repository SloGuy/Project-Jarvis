import unittest
from datetime import timedelta
from decimal import Decimal as D
from app.capital.run_evaluation import parse_time, validate_plan


class PlanTests(unittest.TestCase):
    def test_timezone_and_tick_count(self):
        start = parse_time("2026-09-02T10:00:00-04:00")
        self.assertEqual(start.isoformat(), "2026-09-02T14:00:00+00:00")
        self.assertEqual(validate_plan(
            start, start + timedelta(days=3), 1, "Finnhub",
            "Engineering replay", D("5"), D("5"),
        ), 4320)

    def test_invalid_dates_and_costs(self):
        with self.assertRaises(ValueError):
            parse_time("2026-09-02T14:00:00")
        start = parse_time("2026-09-02T14:00:00Z")
        for duration, fee, purpose in (
            (timedelta(seconds=61), D("5"), "test"),
            (timedelta(days=8), D("5"), "test"),
            (timedelta(hours=1), D("NaN"), "test"),
            (timedelta(hours=1), D("-1"), "test"),
            (timedelta(hours=1), D("5"), " "),
        ):
            with self.subTest(duration=duration, fee=fee):
                with self.assertRaises(ValueError):
                    validate_plan(
                        start, start + duration, 1, "Finnhub",
                        purpose, fee, D("5"),
                    )


if __name__ == "__main__":
    unittest.main()
