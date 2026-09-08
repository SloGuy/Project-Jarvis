"""Analyze verified saved replays without market database access."""
import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from app.capital.offline_verification import verify_report, restore_policy
from app.capital.simulated_ledger import SimulatedLedger, MONEY, QUANTITY


def drawdown(series, starting_cash):
    peak = starting_cash
    maximum = D("0")
    for point in series:
        equity = D(point["equity"])
        peak = max(peak, equity)
        if peak > 0:
            maximum = max(maximum, (peak - equity) / peak * 100)
    return maximum


def analyze_report(report):
    verification = verify_report(report)
    policy = restore_policy(report["policy"])
    initial = policy.starting_capital_usd.quantize(MONEY)
    results = {}
    windows = report["windows"]

    for name, scenario in report["scenarios"].items():
        benchmark = SimulatedLedger(
            initial, scenario["fee_bps"], scenario["slippage_bps"]
        )
        cash, quantity, realized = initial, D("0"), D("0")
        last_price = None
        curve = []
        quality = Counter()
        unusable = Counter()
        exits = Counter()
        rejections = Counter()
        benchmark_entry = None
        closed = []

        for window, event in zip(windows, scenario["events"]):
            snapshot = window["snapshot"]
            at = datetime.fromisoformat(window["decision_at"])
            price = snapshot["latest_price_usd"]
            observed = snapshot["observation_at"]
            if price is not None:
                last_price = D(price)

            fresh = False
            if price is not None and observed is not None:
                age = (at - datetime.fromisoformat(observed)).total_seconds()
                fresh = 0 <= age <= policy.max_price_age_seconds
            if not fresh:
                quality["missing_or_stale_reference_ticks"] += 1
            if not window["risk_only"]:
                quality["regular_ticks"] += 1
                if not snapshot["usable"]:
                    quality["unusable_regular_ticks"] += 1
                    unusable[snapshot["reason"] or "unspecified"] += 1

            if benchmark_entry is None and fresh:
                qty = (initial * D("0.10") / D(price)).quantize(QUANTITY)
                benchmark.buy(quantity=qty, reference_price=D(price))
                benchmark_entry = at.isoformat()

            if event["executed"]:
                fill = event["fill"]
                value, fee, qty = (
                    D(fill["notional"]), D(fill["fee"]), D(fill["quantity"])
                )
                if fill["side"] == "buy":
                    cash -= value + fee
                    quantity += qty
                else:
                    cash += value - fee
                    quantity -= qty
                    pnl = D(fill["realized_pnl"])
                    realized += pnl
                    closed.append(pnl)
                    exits[event["exit_rule"] or "unspecified"] += 1
            elif event["action"] != "hold":
                rejections.update(event["reasons"])

            if last_price is None:
                quality["unmarked_ticks"] += 1
                continue
            equity = cash + (quantity * last_price).quantize(MONEY)
            benchmark_equity = benchmark.mark(last_price)["equity"]
            curve.append({
                "decision_at": at.isoformat(),
                "equity": str(equity),
                "cash": str(cash),
                "quantity": str(quantity),
                "benchmark_equity": str(benchmark_equity),
                "fresh_reference": fresh,
            })

        if not curve:
            raise ValueError("No equity marks available.")
        account = scenario["account"]
        if (
            cash != D(account["cash"])
            or quantity != D(account["quantity"])
            or realized != D(account["realized_pnl"])
            or D(curve[-1]["equity"]) != D(account["equity"])
        ):
            raise ValueError(f"Fill accounting mismatch: {name}")

        final = D(curve[-1]["equity"])
        benchmark_final = D(curve[-1]["benchmark_equity"])
        benchmark_curve = [
            {"equity": point["benchmark_equity"]} for point in curve
        ]
        results[name] = {
            "ending_equity": str(final),
            "return_percent": str((final / initial - 1) * 100),
            "maximum_marked_drawdown_percent": str(drawdown(curve, initial)),
            "completed_trades": len(closed),
            "winning_trades": sum(pnl > 0 for pnl in closed),
            "losing_trades": sum(pnl < 0 for pnl in closed),
            "realized_pnl": str(realized),
            "exit_reasons": dict(exits),
            "rejection_reasons": dict(rejections),
            "data_quality": dict(quality),
            "unusable_reasons": dict(unusable),
            "benchmark": {
                "name": "10% initial allocation buy-and-hold; 90% cash",
                "entry_at": benchmark_entry,
                "ending_equity": str(benchmark_final),
                "return_percent": str((benchmark_final / initial - 1) * 100),
                "maximum_marked_drawdown_percent": str(
                    drawdown(benchmark_curve, initial)
                ),
                "strategy_minus_benchmark_usd": str(final - benchmark_final),
                "fee_bps": scenario["fee_bps"],
                "slippage_bps": scenario["slippage_bps"],
                "forced_liquidation": False,
            },
            "equity_curve": curve,
        }

    return {
        "schema_version": 1,
        "verification": verification,
        "scenarios": results,
        "limitations": [
            "Engineering comparison over a selected development window.",
            "10% buy-and-hold is not matched to strategy timing or risk.",
            "Marked equity includes stale/carry-forward prices, flagged per tick.",
            "Drawdown is measured at sampled marks, not continuous market prices.",
            "Future liquidation costs are not deducted from open positions.",
            "Historical availability and live execution parity remain unverified.",
            "Cash interest and dividends are not separately modeled.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    raw = args.report.read_bytes()
    result = analyze_report(json.loads(raw))
    result["input_report_sha256"] = hashlib.sha256(raw).hexdigest()
    result["analysis_source_sha256"] = hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    output = Path(__file__).resolve().parents[2] / "work" / (
        f"replay_analysis_{uuid4().hex}.json"
    )
    with output.open("x") as handle:
        json.dump(result, handle, indent=2)
    for name, summary in result["scenarios"].items():
        print(name)
        print(json.dumps({
            key: value for key, value in summary.items()
            if key != "equity_curve"
        }, indent=2))
    print("Analysis saved:", output)


if __name__ == "__main__":
    main()
