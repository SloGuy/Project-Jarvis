"""Select provider-timed history visible through validation receipts.

Callers must separately verify the collection chain and retain its
checkpoint. Receipt verification alone does not prove registry authority.
"""

from copy import deepcopy
from datetime import timedelta

from app.capital.validation_plan import timestamp, verify_plan
from app.capital.validation_quote_inputs import (
    select_validation_quote_inputs,
)
from app.capital.validation_quote_receipt import (
    PROVIDERS,
    verify_validation_quote_receipt,
)


class ValidationQuoteHistory:
    def __init__(
        self,
        *,
        directory,
        receipts,
        envelope,
        expected_sha256,
        expected_bound_at,
    ):
        if not isinstance(receipts, (list, tuple)):
            raise ValueError("Supply an ordered receipt sequence.")

        self.directory = directory
        self.envelope = deepcopy(envelope)
        self.plan_sha256 = expected_sha256
        self.bound_at = timestamp(expected_bound_at)

        self.plan = verify_plan(
            self.envelope,
            expected_sha256=self.plan_sha256,
        )

        self.payloads = []
        previous = self.bound_at

        for receipt in receipts:
            payload = verify_validation_quote_receipt(
                receipt=receipt,
                envelope=self.envelope,
                expected_sha256=self.plan_sha256,
                expected_bound_at=self.bound_at.isoformat(),
            )

            recorded = timestamp(payload["recorded_at"])

            if recorded <= previous:
                raise ValueError(
                    "Receipt logging times must increase."
                )

            previous = recorded
            self.payloads.append(payload)

    def window(
        self,
        *,
        decision_at,
        maximum_provider_age,
        maximum_capture_age,
        lookback,
    ):
        decision = timestamp(decision_at)

        if not (
            timestamp(self.plan["start"])
            <= decision
            < timestamp(self.plan["end_exclusive"])
        ):
            raise ValueError(
                "Decision is outside the registered plan window."
            )

        for limit in (
            maximum_provider_age,
            maximum_capture_age,
        ):
            if (
                not isinstance(limit, timedelta)
                or limit <= timedelta(0)
            ):
                raise ValueError(
                    "Age limits must be positive timedeltas."
                )

        # Logging time is an additional visibility boundary.
        # Capture time alone cannot make an unlogged quote available.
        visible = [
            payload
            for payload in self.payloads
            if timestamp(payload["recorded_at"]) < decision
        ]

        result = select_validation_quote_inputs(
            directory=self.directory,
            record_ids=[
                payload["record_id"]
                for payload in visible
            ],
            asset_id=self.plan["asset_id"],
            symbol=self.plan["symbol"],
            provider=PROVIDERS[self.plan["provider"]],
            decision_at=decision.isoformat(),
            maximum_provider_age=maximum_provider_age,
            maximum_capture_age=maximum_capture_age,
            lookback=lookback,
        )

        first_logged_by_id = {}
        first_logged_by_provider_time = {}

        for payload in visible:
            record_id = payload["record_id"]
            first_logged_by_id.setdefault(
                record_id,
                payload["recorded_at"],
            )

            provider_at = payload["record"][
                "provider_observed_at"
            ]

            if provider_at is not None:
                provider_at = timestamp(provider_at).isoformat()
                first_logged_by_provider_time.setdefault(
                    provider_at,
                    payload["recorded_at"],
                )

        for observation in result["observations"]:
            observation["first_recorded_at"] = (
                first_logged_by_provider_time[
                    observation["observed_at"]
                ]
            )

        latest_id = result["latest_record_id"]

        result.update(
            plan_sha256=self.plan_sha256,
            collection_bound_at=self.bound_at.isoformat(),
            latest_recorded_at=(
                first_logged_by_id[latest_id]
                if latest_id is not None
                else None
            ),
            visible_receipt_count=len(visible),
            cutoff_rule=(
                "provider time, capture time, and receipt logging "
                "time must all precede the decision"
            ),
            receipt_bindings_verified=True,
            collection_chain_verified=False,
            registry_authorization_verified=False,
            plan_binding_verified=False,
            execution_authorized=False,
            live_capital_authorized=False,
        )

        return result
