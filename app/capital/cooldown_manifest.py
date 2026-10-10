"""Configuration fingerprint for the development cooldown comparison."""

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
import hashlib
import json
from pathlib import Path

from app.capital.replay_manifest import capture_replay_manifest
from app.capital.validation_input_contract import capture_input_sources
from app.capital.simulated_ledger import number


ROOT = Path(__file__).resolve().parents[2]

COMPARISON_SOURCES = (
    "app/capital/cooldown_comparison.py",
    "app/capital/cooldown_provider_comparison.py",
    "app/capital/cooldown_manifest.py",
    "app/capital/cooldown_verification.py",
    "app/capital/cooldown_analysis.py",
    "app/capital/cooldown_assessment.py",
    "app/capital/cooldown_contract.py",
    "app/capital/cooldown_registration.py",
    "app/capital/autonomy_trade_research.py",
    "app/capital/trade_research_runner.py",
    "app/capital/trade_research_design.py",
)


def _plain(value):
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Nonfinite configuration value.")
        return str(value)
    if isinstance(value, timedelta):
        return {"seconds": str(value.total_seconds())}
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("Configuration timestamp needs timezone.")
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _digest(value):
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def capture_cooldown_manifest(*, policy, fee_bps, slippage_bps):
    for value in (fee_bps, slippage_bps):
        if isinstance(value, bool):
            raise ValueError("Costs must be numeric, not boolean.")

    fee = number(fee_bps)
    slippage = number(slippage_bps)
    if fee >= 10000 or slippage >= 10000:
        raise ValueError("Costs must be below 10,000 bps.")

    manifest = {
        "schema_version": 1,
        "kind": "mean_reversion_v2_loss_cooldown_comparison",
        "replay_manifest": capture_replay_manifest(),
        "provider_source_sha256": capture_input_sources(),
        "comparison_source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in COMPARISON_SOURCES
        },
        "policy": _plain(asdict(policy)),
        "fee_bps": str(fee),
        "slippage_bps": str(slippage),
        "comparison_rules": {
            "baseline_cooldown_enabled": False,
            "intervention_cooldown_enabled": True,
            "cooldown_seconds": 3600,
            "trigger": "own_completed_trade_net_realized_pnl_below_zero",
            "blocked_action": "new_same_asset_entry",
            "boundary": "entry_allowed_at_or_after_cooldown_until",
            "confirmations": "original_rules_continue_during_cooldown",
            "protective_exits": "unchanged",
            "accounts": "independent",
            "inputs_and_costs": "identical_between_accounts",
        },
        "scope": "configuration_fingerprint",
        "registration_verified": False,
        "input_availability_verified": False,
        "promotion_authorized": False,
        "live_capital_authorized": False,
    }
    manifest["manifest_sha256"] = _digest(manifest)
    return deepcopy(manifest)


def verify_cooldown_manifest(
    saved, *, policy, fee_bps, slippage_bps
):
    current = capture_cooldown_manifest(
        policy=policy, fee_bps=fee_bps, slippage_bps=slippage_bps
    )
    if saved != current:
        raise ValueError("Cooldown comparison configuration changed.")
    return deepcopy(current)
