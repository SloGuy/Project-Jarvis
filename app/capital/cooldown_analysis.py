"""Calculate marked metrics from a verified cooldown comparison."""

from collections import Counter
from datetime import datetime
from decimal import Decimal as D

from app.capital.cooldown_verification import verify_cooldown_packet
from app.capital.replay_analysis import drawdown
from app.capital.simulated_ledger import SimulatedLedger, MONEY, QUANTITY


def _metrics(report, name, policy):
    initial = policy.starting_capital_usd.quantize(MONEY)
    benchmark = SimulatedLedger(
        initial, report["fee_bps"], report["slippage_bps"]
    )
    account = report[name]
    cash, quantity, realized = initial, D("0"), D("0")
    last_price = None
    benchmark_entry = None
    curve = []
    quality = Counter()
    exits = Counter()
    closed = []

    for window, event in zip(report["windows"], account["events"]):
        snapshot = window["snapshot"]
        at = datetime.fromisoformat(window["decision_at"])
        raw_price = snapshot["latest_price_usd"]
        observed = snapshot["observation_at"]
        price = D(str(raw_price)) if raw_price is not None else None

        if price is not None:
            last_price = price

        fresh = False
        if price is not None and observed is not None:
            age = (
                at - datetime.fromisoformat(observed)
            ).total_seconds()
            fresh = 0 <= age <= policy.max_price_age_seconds

        if not fresh:
            quality["missing_or_stale_reference_ticks"] += 1
        if not window["risk_only"]:
            quality["regular_ticks"] += 1
            if not snapshot["usable"]:
                quality["unusable_regular_ticks"] += 1

        if benchmark_entry is None and fresh:
            qty = (initial * D("0.10") / price).quantize(QUANTITY)
            benchmark.buy(quantity=qty, reference_price=price)
            benchmark_entry = at.isoformat()

        if event["executed"]:
            fill = event["fill"]
            value = D(str(fill["notional"]))
            fee = D(str(fill["fee"]))
            qty = D(str(fill["quantity"]))
            if fill["side"] == "buy":
                cash -= value + fee
                quantity += qty
            else:
                cash += value - fee
                quantity -= qty
                pnl = D(str(fill["realized_pnl"]))
                realized += pnl
                closed.append(pnl)
                exits[event["exit_rule"] or "unspecified"] += 1

        if last_price is None:
            quality["unmarked_ticks"] += 1
            continue

        equity = cash + (quantity * last_price).quantize(MONEY)
        curve.append({
            "decision_at": at.isoformat(),
            "equity": str(equity),
            "cash": str(cash),
            "quantity": str(quantity),
            "benchmark_equity": str(benchmark.mark(last_price)["equity"]),
            "fresh_reference": fresh,
        })

    if (
        cash != D(str(account["cash_usd"]))
        or quantity != D(str(account["open_quantity"]))
        or realized != D(str(account["net_realized_usd"]))
        or len(closed) != account["closed_trade_count"]
    ):
        raise ValueError(f"Comparison accounting mismatch: {name}")

    if not curve and quantity != 0:
        raise ValueError("An open position has no valuation price.")

    # With no prices and no position, only cash is known.
    # Missing marks and benchmark entry remain explicit below.
    final = D(curve[-1]["equity"]) if curve else cash
    benchmark_final = (
        D(curve[-1]["benchmark_equity"]) if curve else initial
    )
    benchmark_curve = [
        {"equity": point["benchmark_equity"]} for point in curve
    ]

    return {
        "ending_equity": str(final),
        "return_percent": str((final / initial - 1) * 100),
        "maximum_marked_drawdown_percent": (
            str(drawdown(curve, initial)) if curve else None
        ),
        "completed_trades": len(closed),
        "winning_trades": sum(pnl > 0 for pnl in closed),
        "losing_trades": sum(pnl < 0 for pnl in closed),
        "realized_pnl": str(realized),
        "profit_factor": account["profit_factor"],
        "profit_factor_status": account["profit_factor_status"],
        "exit_reasons": dict(exits),
        "data_quality": dict(quality),
        "has_equity_marks": bool(curve),
        "benchmark": {
            "name": "10% initial allocation buy-and-hold; 90% cash",
            "entry_at": benchmark_entry,
            "ending_equity": str(benchmark_final),
            "return_percent": str((benchmark_final / initial - 1) * 100),
            "maximum_marked_drawdown_percent": (
                str(drawdown(benchmark_curve, initial))
                if benchmark_curve else None
            ),
            "strategy_minus_benchmark_usd": str(final - benchmark_final),
            "fee_bps": report["fee_bps"],
            "slippage_bps": report["slippage_bps"],
            "forced_liquidation": False,
        },
        "equity_curve": curve,
    }


def analyze_cooldown_packet(report, *, policy, **expected_references):
    verification = verify_cooldown_packet(
        report, policy=policy, **expected_references
    )
    accounts = {
        name: _metrics(report, name, policy)
        for name in ("baseline", "intervention")
    }
    return {
        "schema_version": 1,
        "verification": verification,
        "accounts": accounts,
        "input_availability_verified": all(
            bool(window["observation_ids"])
            for window in report["windows"]
        ),
        "comparison_preregistered": False,
        "promotion_authorized": False,
        "live_capital_authorized": False,
        "limitations": [
            "Development comparison; acceptance is not preregistered.",
            "Benchmark allocation is not matched to strategy timing or risk.",
            "Stale carry-forward marks are counted and flagged.",
            "Drawdown uses sampled marks, not continuous market prices.",
            "Open-position liquidation costs are not deducted.",
            "Realized profit factor excludes open-position outcomes.",
            "Local receipts do not independently authenticate provider data.",
            "Cash interest and dividends are not separately modeled.",
        ],
    }
