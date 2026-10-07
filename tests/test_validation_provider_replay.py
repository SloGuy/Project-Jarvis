"""Test provider-history adaptation without database access."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from app.capital.mean_reversion_math import LOOKBACK_OBSERVATIONS
from app.capital.validation_provider_replay import ProviderReplayHistory


MODULE = "app.capital.validation_provider_replay"


class ProviderReplayTests(unittest.TestCase):
    def setUp(self):
        self.decision = datetime(
            2026, 10, 7, 12, 0, tzinfo=timezone.utc
        )

        self.patch = patch(
            MODULE + ".ValidationQuoteHistory"
        )
        self.history_class = self.patch.start()
        self.addCleanup(self.patch.stop)

        self.history = self.history_class.return_value
        self.history.plan = {
            "schema_version": 2,
            "symbol": "BTC",
            "input_contract": {
                "maximum_provider_age_seconds": 120,
                "maximum_capture_age_seconds": 60,
            },
        }

    def adapter(self):
        return ProviderReplayHistory(
            directory="/unused",
            receipts=[],
            envelope={"fixture": True},
            expected_sha256="a" * 64,
            expected_bound_at="2026-10-07T11:00:00+00:00",
        )

    def observations(self, count=20):
        return [
            {
                "price_usd": str(
                    Decimal("100.12345678") + Decimal(index)
                ),
                "observed_at": (
                    self.decision
                    - timedelta(seconds=30 + index * 60)
                ).isoformat(),
                "record_id": f"{index + 1:064x}",
                "first_recorded_at": (
                    self.decision
                    - timedelta(seconds=20 + index * 60)
                ).isoformat(),
            }
            for index in range(count)
        ]

    def select(self, observations):
        selected = {
            "observations": observations,
            "receipt_bindings_verified": True,
            "collection_chain_verified": False,
            "registry_authorization_verified": False,
        }
        self.history.window.return_value = selected
        return selected

    def test_registered_limits_and_lookback_are_used(self):
        self.select(self.observations())
        self.adapter().window(decision_at=self.decision)

        self.history.window.assert_called_once_with(
            decision_at=self.decision,
            maximum_provider_age=timedelta(seconds=120),
            maximum_capture_age=timedelta(seconds=60),
            lookback=LOOKBACK_OBSERVATIONS,
        )

    def test_provider_timestamp_becomes_snapshot_time(self):
        observations = self.observations()
        self.select(observations)

        result = self.adapter().window(
            decision_at=self.decision
        )

        self.assertEqual(
            result["snapshot"].observation_at,
            datetime.fromisoformat(
                observations[0]["observed_at"]
            ),
        )
        self.assertTrue(result["snapshot"].usable)

    def test_price_precision_is_preserved(self):
        self.select(self.observations())

        snapshot = self.adapter().window(
            decision_at=self.decision
        )["snapshot"]

        self.assertEqual(
            snapshot.latest_price_usd,
            Decimal("100.12345678"),
        )
        self.assertEqual(
            snapshot.mean_price_usd,
            Decimal("109.62345678"),
        )

    def test_record_hashes_identify_selected_inputs(self):
        observations = self.observations()
        selected = self.select(observations)

        result = self.adapter().window(
            decision_at=self.decision
        )

        self.assertEqual(
            result["observation_ids"],
            [row["record_id"] for row in observations],
        )
        self.assertEqual(result["provider_input"], selected)
        self.assertTrue(result["selected_values_witnessed"])

    def test_ineligible_selection_does_not_fall_back(self):
        self.select([])

        result = self.adapter().window(
            decision_at=self.decision
        )

        self.assertFalse(result["snapshot"].usable)
        self.assertIsNone(result["snapshot"].latest_price_usd)
        self.assertEqual(result["observation_ids"], [])
        self.assertFalse(result["selected_values_witnessed"])

    def test_insufficient_history_remains_unusable(self):
        self.select(self.observations(count=19))

        result = self.adapter().window(
            decision_at=self.decision
        )

        self.assertEqual(
            result["snapshot"].observation_count, 19
        )
        self.assertFalse(result["snapshot"].usable)
        self.assertIsNone(result["snapshot"].latest_price_usd)

    def test_adapter_does_not_mutate_selected_observations(self):
        observations = self.observations()
        original = deepcopy(observations)
        self.select(observations)

        self.adapter().window(decision_at=self.decision)

        self.assertEqual(observations, original)

    def test_legacy_plan_is_rejected(self):
        self.history.plan["schema_version"] = 1

        with self.assertRaises(ValueError):
            self.adapter()

    def test_adapter_does_not_claim_authority(self):
        self.select(self.observations())

        result = self.adapter().window(
            decision_at=self.decision
        )

        for field in (
            "collection_chain_verified",
            "registry_authorization_verified",
            "execution_authorized",
            "live_capital_authorized",
        ):
            self.assertIs(result[field], False)


if __name__ == "__main__":
    unittest.main()
