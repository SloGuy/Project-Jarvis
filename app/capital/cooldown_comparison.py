"""Development comparison of baseline and loss-cooldown replay.

This is an in-memory comparison, not a registered validation packet.
"""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal

from app.capital.position_simulation import PositionSimulation


def _validate_inputs(decision_inputs, symbol):
    if not isinstance(decision_inputs, list) or not decision_inputs:
        raise ValueError("A nonempty decision-input list is required.")
    if len(decision_inputs) > 10000:
        raise ValueError("Limit each comparison to 10,000 decisions.")

    previous = None
    for row in decision_inputs:
        if not isinstance(row, dict) or set(row) != {
            "snapshot", "decision_at", "risk_only"
        }:
            raise ValueError("Invalid decision-input structure.")

        at = row["decision_at"]
        if not isinstance(at, datetime) or at.utcoffset() is None:
            raise ValueError("Decision time must include timezone.")
        if previous is not None and at <= previous:
            raise ValueError("Decision times must strictly increase.")
        previous = at

        if type(row["risk_only"]) is not bool:
            raise ValueError("risk_only must be a boolean.")

        snapshot = row["snapshot"]
        if snapshot.symbol != symbol:
            raise ValueError("Snapshot symbol mismatch.")
        observed = snapshot.observation_at
        if observed is not None:
            if observed.utcoffset() is None:
                raise ValueError("Observation time must include timezone.")
            if observed >= at:
                raise ValueError("Observation must precede decision.")


def _result(simulation):
    ledger = simulation.ledger
    completed = [
        fill for fill in ledger.fills if fill["side"] == "sell"
    ]
    pnls = [fill["realized_pnl"] for fill in completed]
    zero = Decimal("0")
    gross_profit = sum((value for value in pnls if value > 0), zero)
    gross_loss = -sum((value for value in pnls if value < 0), zero)

    return {
        "events": deepcopy(simulation.events),
        "fills": deepcopy(ledger.fills),
        "closed_trade_count": len(completed),
        "gross_profit_usd": gross_profit,
        "gross_loss_usd": gross_loss,
        "net_realized_usd": ledger.realized_pnl,
        "profit_factor": (
            gross_profit / gross_loss if gross_loss > 0 else None
        ),
        "profit_factor_status": (
            "defined" if gross_loss > 0 else "no_realized_losses"
        ),
        "total_fees_usd": ledger.total_fees,
        "cash_usd": ledger.cash,
        "open_quantity": ledger.quantity,
        "open_entry_cost_usd": ledger.entry_cost,
        "cooldown_until": simulation.cooldown_until,
    }


def compare_loss_cooldown(
    *, symbol, policy, fee_bps, slippage_bps, decision_inputs
):
    """Replay one asset in independent baseline and intervention accounts.

    Only each intervention account's own completed, cost-adjusted losses
    can start its cooldown. Open positions are not forcibly liquidated.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Symbol is required.")
    symbol = symbol.strip().upper()

    inputs = deepcopy(decision_inputs)
    _validate_inputs(inputs, symbol)

    baseline = PositionSimulation(
        symbol=symbol,
        policy=deepcopy(policy),
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )
    intervention = PositionSimulation(
        symbol=symbol,
        policy=deepcopy(policy),
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        loss_reentry_cooldown=True,
    )

    for row in inputs:
        for simulation in (baseline, intervention):
            simulation.step(
                deepcopy(row["snapshot"]),
                decision_at=row["decision_at"],
                risk_only=row["risk_only"],
            )

    return {
        "scope": "development_same_asset_cooldown_comparison",
        "strategy_name": "mean_reversion_v2",
        "symbol": symbol,
        "decision_count": len(inputs),
        "cooldown_seconds": 3600,
        "fee_bps": baseline.ledger.fee_bps,
        "slippage_bps": baseline.ledger.slippage_bps,
        "starting_capital_usd": baseline.ledger.starting_cash,
        "baseline": _result(baseline),
        "intervention": _result(intervention),
        "limitations": [
            "Input availability and provenance are not verified here.",
            "Sources and acceptance criteria are not registration-pinned.",
            "Realized profit factor excludes remaining open positions.",
            "A comparison does not establish a performance benefit.",
        ],
        "validation_ready": False,
        "promotion_authorized": False,
        "strategy_change_authorized": False,
        "live_capital_authorized": False,
    }
