"""Current simulation configuration fingerprint; not a launch archive."""
from decimal import getcontext
import hashlib
import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SOURCE_FILES = (
    "app/capital/position_simulation.py",
    "app/capital/simulated_ledger.py",
    "app/capital/signal_replay.py",
    "app/capital/mean_reversion_math.py",
    "app/capital/replay_manifest.py",
    "app/autonomous_trading/mean_reversion_v2_strategy.py",
    "app/autonomous_trading/mean_reversion_v2_exit.py",
    "app/autonomous_trading/signal_confirmation.py",
    "app/autonomous_trading/strategy.py",
    "app/autonomous_trading/proposals.py",
    "app/autonomous_trading/policy.py",
    "app/autonomous_trading/risk_governor.py",
    "app/autonomous_trading/exit_rules.py",
    "app/autonomous_trading/asset_risk_metadata.py",
)


def capture_replay_manifest():
    from app.autonomous_trading import mean_reversion_v2_strategy as strategy
    from app.autonomous_trading.mean_reversion_v2_exit import MAX_HOLDING_TIME
    from app.autonomous_trading.signal_confirmation import REQUIRED_CONFIRMATIONS
    from app.capital.simulated_ledger import MONEY, QUANTITY

    context = getcontext()
    return {
        "schema_version": 1,
        "python": platform.python_version(),
        "strategy_constants": {
            name: str(getattr(strategy, name))
            for name in (
                "LOOKBACK_OBSERVATIONS", "MINIMUM_OBSERVATIONS",
                "ENTRY_Z_SCORE", "RECOVERY_Z_SCORE",
                "DEFAULT_POSITION_PERCENT", "BASE_CONFIDENCE_PERCENT",
                "MAX_CONFIDENCE_PERCENT",
            )
        },
        "required_confirmations": REQUIRED_CONFIRMATIONS,
        "maximum_holding_seconds": str(MAX_HOLDING_TIME.total_seconds()),
        "money_quantum": str(MONEY),
        "quantity_quantum": str(QUANTITY),
        "decimal_context": {
            "precision": context.prec,
            "rounding": context.rounding,
            "Emin": context.Emin,
            "Emax": context.Emax,
            "capitals": context.capitals,
            "clamp": context.clamp,
            "traps": sorted(
                signal.__name__
                for signal, enabled in context.traps.items() if enabled
            ),
        },
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in SOURCE_FILES
        },
        "scope": "Core snapshot-to-position simulation sources and settings.",
        "limitations": [
            "Hashes identify source files but do not archive them.",
            "Not a complete external-package or operating-system manifest.",
            "Data collection and historical availability remain unverified.",
        ],
    }


def verify_replay_manifest(saved):
    current = capture_replay_manifest()
    if saved != current:
        differing = sorted(
            key for key in set(saved) | set(current)
            if saved.get(key) != current.get(key)
        )
        raise ValueError(
            "Replay configuration mismatch: " + ", ".join(differing)
        )
