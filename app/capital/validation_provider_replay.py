"""Build strategy snapshots from receipt-visible provider quotes.

Collection-chain and registry checks belong to the caller.
This adapter neither claims plans nor authorizes execution.
"""

from datetime import timedelta

from app.capital.mean_reversion_math import (
    LOOKBACK_OBSERVATIONS,
    calculate_mean_reversion_snapshot,
)
from app.capital.validation_quote_history import ValidationQuoteHistory


class ProviderReplayHistory:
    def __init__(
        self,
        *,
        directory,
        receipts,
        envelope,
        expected_sha256,
        expected_bound_at,
    ):
        self.history = ValidationQuoteHistory(
            directory=directory,
            receipts=receipts,
            envelope=envelope,
            expected_sha256=expected_sha256,
            expected_bound_at=expected_bound_at,
        )

        self.plan = self.history.plan

        if self.plan["schema_version"] != 2:
            raise ValueError(
                "Provider replay requires a version-two plan."
            )

        contract = self.plan["input_contract"]

        self.maximum_provider_age = timedelta(
            seconds=contract["maximum_provider_age_seconds"]
        )
        self.maximum_capture_age = timedelta(
            seconds=contract["maximum_capture_age_seconds"]
        )

    def window(self, *, decision_at):
        selected = self.history.window(
            decision_at=decision_at,
            maximum_provider_age=self.maximum_provider_age,
            maximum_capture_age=self.maximum_capture_age,
            lookback=LOOKBACK_OBSERVATIONS,
        )

        observations = selected["observations"]

        # Keep Decimal-compatible price strings throughout arithmetic.
        # An ineligible latest quote supplies no observations; older
        # quotes must not become an alternative fresh input.
        snapshot = calculate_mean_reversion_snapshot(
            symbol=self.plan["symbol"],
            observations=[
                {
                    "price_usd": observation["price_usd"],
                    "observed_at": observation["observed_at"],
                }
                for observation in observations
            ],
        )

        return {
            "snapshot": snapshot,
            # These are provenance record hashes, not database row IDs.
            "observation_ids": [
                observation["record_id"]
                for observation in observations
            ],
            "provider_input": selected,
            "selected_values_witnessed": bool(observations),
            "collection_chain_verified": False,
            "registry_authorization_verified": False,
            "execution_authorized": False,
            "live_capital_authorized": False,
        }
