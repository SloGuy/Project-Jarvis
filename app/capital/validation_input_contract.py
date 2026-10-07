"""Explicit provider-time input contracts for future validation plans.

Legacy plans do not acquire this contract automatically.
Source hashes identify code; they do not authenticate market data.
"""

from copy import deepcopy
import hashlib
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]

SOURCE_FILES = (
    "app/capital/autonomy_validation_worker.py",
    "app/capital/autonomy_validation_evaluation.py",
    "app/capital/validation_research.py",
    "app/capital/validation_provider_replay.py",
    "app/capital/validation_provider_verification.py",
    "app/capital/witness_verification.py",
    "app/capital/validation_input_contract.py",
    "app/capital/validation_provider_capture.py",
    "app/capital/validation_plan.py",
    "app/capital/validation_registry.py",
    "app/capital/validation_provider_collection.py",
    "app/capital/validation_quote_inputs.py",
    "app/capital/validation_quote_receipt.py",
    "app/capital/validation_quote_store.py",
    "app/capital/validation_quote_history.py",
    "app/capital/quote_provenance.py",
    "app/capital/quote_provenance_adapter.py",
    "app/capital/quote_provenance_capture.py",
    "app/capital/quote_provenance_crypto_batch.py",
    "app/capital/quote_provenance_eligibility.py",
    "app/capital/quote_provenance_store.py",
    "app/capital/receipt_store.py",
    "app/capital/observation_witness.py",
    "app/capital/portfolio_daily_returns.py",
    "app/capital/run_evaluation.py",
    "app/capital/run_validation.py",
    "app/capital/offline_verification.py",
    "app/capital/replay_analysis.py",
    "app/capital/validation_assessment.py",
    "app/capital/autonomy_validation_collection.py",
    "app/capital/autonomy_validation_plan.py",
    "app/watchlist_quotes.py",
)

FIELDS = {
    "schema_version",
    "kind",
    "maximum_provider_age_seconds",
    "maximum_capture_age_seconds",
    "capture_interval_seconds",
    "source_sha256",
}


def capture_input_sources():
    return {
        name: hashlib.sha256(
            (ROOT / name).read_bytes()
        ).hexdigest()
        for name in SOURCE_FILES
    }


def _positive_integer(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(
            f"{name} must be a positive integer."
        )
    return value


def validate_input_contract(
    contract,
    *,
    policy,
    verify_sources=False,
):
    if (
        not isinstance(contract, dict)
        or set(contract) != FIELDS
    ):
        raise ValueError("Unexpected input-contract fields.")

    if (
        type(contract["schema_version"]) is not int
        or contract["schema_version"] != 1
    ):
        raise ValueError("Unsupported input-contract schema.")

    if contract["kind"] != "provider_time_v1":
        raise ValueError("Unsupported validation input kind.")

    if not isinstance(policy, dict):
        raise ValueError("Policy must be a dictionary.")

    policy_age = _positive_integer(
        policy.get("max_price_age_seconds"),
        "policy max_price_age_seconds",
    )

    provider_age = _positive_integer(
        contract["maximum_provider_age_seconds"],
        "maximum_provider_age_seconds",
    )

    capture_age = _positive_integer(
        contract["maximum_capture_age_seconds"],
        "maximum_capture_age_seconds",
    )

    interval = _positive_integer(
        contract["capture_interval_seconds"],
        "capture_interval_seconds",
    )

    if provider_age != policy_age:
        raise ValueError(
            "Provider-age limit must match the registered risk policy."
        )

    if capture_age > provider_age:
        raise ValueError(
            "Capture-age limit must not exceed provider-age limit."
        )

    if interval * 2 > min(provider_age, capture_age):
        raise ValueError(
            "Capture interval requires at least twofold timing margin."
        )

    sources = contract["source_sha256"]

    if (
        not isinstance(sources, dict)
        or set(sources) != set(SOURCE_FILES)
    ):
        raise ValueError("Input source manifest is incomplete.")

    for name, value in sources.items():
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{64}", value) is None
        ):
            raise ValueError(
                f"Invalid source fingerprint: {name}"
            )

    if type(verify_sources) is not bool:
        raise ValueError("verify_sources must be a boolean.")

    if verify_sources and sources != capture_input_sources():
        raise ValueError(
            "Provider-time input sources changed."
        )

    return deepcopy(contract)


def make_input_contract(
    *,
    policy,
    capture_interval_seconds=60,
    maximum_capture_age_seconds=None,
):
    if not isinstance(policy, dict):
        raise ValueError("Policy must be a dictionary.")

    provider_age = _positive_integer(
        policy.get("max_price_age_seconds"),
        "policy max_price_age_seconds",
    )

    if maximum_capture_age_seconds is None:
        maximum_capture_age_seconds = provider_age

    contract = {
        "schema_version": 1,
        "kind": "provider_time_v1",
        "maximum_provider_age_seconds": provider_age,
        "maximum_capture_age_seconds": (
            maximum_capture_age_seconds
        ),
        "capture_interval_seconds": capture_interval_seconds,
        "source_sha256": capture_input_sources(),
    }

    return validate_input_contract(
        contract,
        policy=policy,
        verify_sources=True,
    )
