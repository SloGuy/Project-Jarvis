"""Tests for provider-timed validation input selection."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.capital.quote_provenance import (
    make_quote_provenance,
)
from app.capital.quote_provenance_store import (
    save_quote_provenance,
)
from app.capital.validation_quote_inputs import (
    select_validation_quote_inputs,
)


class ValidationQuoteInputsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.at = datetime(
            2026, 10, 6, 18, 0,
            tzinfo=timezone.utc,
        )

    def quote(
        self,
        provider_age,
        capture_age,
        price="60000",
        asset_id=1,
    ):
        provider_timestamp = None

        if provider_age is not None:
            provider_timestamp = int(
                (
                    self.at
                    - timedelta(seconds=provider_age)
                ).timestamp()
            )

        record = make_quote_provenance(
            asset_id=asset_id,
            symbol="BTC",
            asset_type="crypto",
            provider="CoinGecko REST",
            price_usd=price,
            provider_timestamp=provider_timestamp,
            captured_at=(
                self.at
                - timedelta(seconds=capture_age)
            ),
        )

        saved = save_quote_provenance(
            directory=self.directory,
            record=record,
        )

        return saved["record_id"]

    def select(self, ids):
        return select_validation_quote_inputs(
            directory=self.directory,
            record_ids=ids,
            asset_id=1,
            symbol="BTC",
            provider="CoinGecko REST",
            decision_at=self.at,
            maximum_provider_age=timedelta(
                seconds=120,
            ),
            maximum_capture_age=timedelta(
                seconds=120,
            ),
            lookback=60,
        )

    def test_fresh_quote(self):
        result = self.select([
            self.quote(60, 10),
        ])

        self.assertEqual(
            result["status"],
            "eligible",
        )
        self.assertEqual(
            len(result["observations"]),
            1,
        )

    def test_provider_age_boundary(self):
        result = self.select([
            self.quote(120, 10),
        ])
        self.assertEqual(
            result["status"],
            "eligible",
        )

        result = self.select([
            self.quote(121, 10),
        ])
        self.assertEqual(
            result["status"],
            "ineligible",
        )

    def test_recapture_does_not_refresh_provider_time(self):
        result = self.select([
            self.quote(300, 1),
        ])

        self.assertIn(
            "provider_quote_too_old",
            result["eligibility"]["reasons"],
        )

    def test_missing_time_does_not_fall_back(self):
        result = self.select([
            self.quote(60, 20),
            self.quote(None, 10),
        ])

        self.assertEqual(
            result["status"],
            "ineligible",
        )
        self.assertIn(
            "provider_timestamp_missing",
            result["eligibility"]["reasons"],
        )

    def test_capture_must_precede_decision(self):
        for capture_age in (0, -1):
            with self.subTest(
                capture_age=capture_age,
            ):
                result = self.select([
                    self.quote(30, capture_age),
                ])

                self.assertEqual(
                    result["status"],
                    "unavailable",
                )

    def test_repeated_quote_counts_once(self):
        result = self.select([
            self.quote(60, 20),
            self.quote(60, 10),
        ])

        self.assertEqual(
            len(result["observations"]),
            1,
        )
        self.assertEqual(
            result["observations"][0][
                "first_captured_at"
            ],
            (
                self.at
                - timedelta(seconds=20)
            ).isoformat(),
        )

    def test_conflicting_quote_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "Conflicting",
        ):
            self.select([
                self.quote(60, 20, "60000"),
                self.quote(60, 10, "60001"),
            ])

    def test_tampered_file_is_rejected(self):
        ident = self.quote(60, 10)
        path = self.directory / f"{ident}.json"

        path.write_bytes(
            path.read_bytes().replace(
                b"60000",
                b"60001",
            )
        )

        with self.assertRaises(ValueError):
            self.select([ident])

    def test_future_provider_time_is_rejected(self):
        with self.assertRaises(ValueError):
            self.quote(-30, 10)

    def test_wrong_asset_is_rejected(self):
        ident = self.quote(
            60,
            10,
            asset_id=2,
        )

        with self.assertRaisesRegex(
            ValueError,
            "identity",
        ):
            self.select([ident])

    def test_future_capture_cannot_change_past_selection(self):
        visible = self.quote(60, 10)
        future = self.quote(60, -10, "60001")

        result = self.select([
            visible,
            future,
        ])

        self.assertEqual(
            result["status"],
            "eligible",
        )
        self.assertEqual(
            result["observations"][0]["price_usd"],
            "60000",
        )


if __name__ == "__main__":
    unittest.main()
