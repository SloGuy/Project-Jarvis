from collections import defaultdict
from statistics import mean

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import (
    AutonomousTradeJournal,
    MarketAsset,
)


STRATEGY_NAME = "mean_reversion_v1"
PORTFOLIO_ID = 3


def safe_float(value):
    if value is None:
        return None
    return float(value)


def profit_factor(pnls):
    gross_profit = sum(x for x in pnls if x > 0)
    gross_loss = abs(sum(x for x in pnls if x < 0))

    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0

    return gross_profit / gross_loss


def main():
    with SessionLocal() as session:
        rows = session.execute(
            select(
                AutonomousTradeJournal,
                MarketAsset.symbol,
            )
            .join(
                MarketAsset,
                MarketAsset.id
                == AutonomousTradeJournal.asset_id,
            )
            .where(
                AutonomousTradeJournal.portfolio_id
                == PORTFOLIO_ID,
                AutonomousTradeJournal.strategy_name
                == STRATEGY_NAME,
                AutonomousTradeJournal.status
                == "closed",
            )
            .order_by(
                AutonomousTradeJournal.closed_at.asc()
            )
        ).all()

    if not rows:
        print("No closed mean-reversion trades found.")
        return

    trades = []

    for journal, symbol in rows:
        entry_context = journal.entry_market_context or {}
        exit_context = journal.exit_market_context or {}

        trades.append(
            {
                "id": journal.id,
                "symbol": symbol,
                "entry_price": safe_float(journal.entry_price_usd),
                "exit_price": safe_float(journal.exit_price_usd),
                "entry_mean": safe_float(
                    entry_context.get("mean_price_usd")
                ),
                "exit_mean": safe_float(
                    exit_context.get("mean_price_usd")
                ),
                "pnl": safe_float(
                    journal.realized_gain_loss_usd
                ) or 0.0,
                "return_percent": safe_float(
                    journal.return_percent
                ),
                "holding_seconds": (
                    journal.holding_duration_seconds
                ),
                "exit_rule": (
                    journal.exit_rule or "unknown"
                ),
                "thesis_correct": journal.thesis_correct,
                "entry_z": entry_context.get("z_score"),
                "exit_z": exit_context.get("z_score"),
                "opened_at": journal.opened_at,
                "closed_at": journal.closed_at,
            }
        )

    pnls = [trade["pnl"] for trade in trades]

    winners = [
        trade for trade in trades
        if trade["pnl"] > 0
    ]

    losers = [
        trade for trade in trades
        if trade["pnl"] < 0
    ]

    flats = [
        trade for trade in trades
        if trade["pnl"] == 0
    ]

    active_days = {
        trade["opened_at"].date()
        for trade in trades
        if trade["opened_at"] is not None
    }

    avg_winner = (
        mean(trade["pnl"] for trade in winners)
        if winners else 0.0
    )

    avg_loser = (
        mean(trade["pnl"] for trade in losers)
        if losers else 0.0
    )

    holding_times = [
        trade["holding_seconds"]
        for trade in trades
        if trade["holding_seconds"] is not None
    ]

    entry_z_scores = [
        float(trade["entry_z"])
        for trade in trades
        if trade["entry_z"] is not None
    ]

    exit_z_scores = [
        float(trade["exit_z"])
        for trade in trades
        if trade["exit_z"] is not None
    ]

    print()
    print("MEAN REVERSION V1 — DIAGNOSTIC")
    print("=" * 60)

    print("\nTRADES")
    print(f"Closed trades:       {len(trades)}")
    print(f"Winners:             {len(winners)}")
    print(f"Losers:              {len(losers)}")
    print(f"Flat trades:         {len(flats)}")
    print(
        f"Win rate:            "
        f"{len(winners) / len(trades) * 100:.2f}%"
    )
    print(f"Active trade days:   {len(active_days)}")

    if active_days:
        print(
            f"Trades / active day: "
            f"{len(trades) / len(active_days):.2f}"
        )

    print("\nECONOMICS")
    print(f"Total realized P&L:  ${sum(pnls):.4f}")
    print(f"Average P&L/trade:   ${mean(pnls):.4f}")
    print(f"Average winner:      ${avg_winner:.4f}")
    print(f"Average loser:       ${avg_loser:.4f}")

    payoff_ratio = (
        avg_winner / abs(avg_loser)
        if avg_loser != 0 else float("inf")
    )

    print(f"Payoff ratio:        {payoff_ratio:.3f}")
    print(f"Profit factor:       {profit_factor(pnls):.3f}")
    print(f"Best trade:          ${max(pnls):.4f}")
    print(f"Worst trade:         ${min(pnls):.4f}")

    print("\nBEHAVIOR")

    if holding_times:
        avg_minutes = mean(holding_times) / 60
        print(f"Avg holding time:    {avg_minutes:.2f} min")

    if entry_z_scores:
        print(
            f"Average entry Z:     "
            f"{mean(entry_z_scores):.3f}"
        )

    if exit_z_scores:
        print(
            f"Average exit Z:      "
            f"{mean(exit_z_scores):.3f}"
        )

    thesis_true = sum(
        trade["thesis_correct"] is True
        for trade in trades
    )

    thesis_false = sum(
        trade["thesis_correct"] is False
        for trade in trades
    )

    thesis_unknown = len(trades) - thesis_true - thesis_false

    print(f"Z-score recovered:   {thesis_true}")
    print(f"Z-score failed:      {thesis_false}")
    print(f"Z-score unknown:     {thesis_unknown}")

    recovery_trades = [
        trade for trade in trades
        if trade["exit_rule"] == "mean_recovery"
    ]

    print("\nRECOVERY EXIT OUTCOMES")
    print(
        "Profitable: "
        f"{sum(t['pnl'] > 0 for t in recovery_trades)}"
    )
    print(
        "Losing:     "
        f"{sum(t['pnl'] < 0 for t in recovery_trades)}"
    )
    print(
        "Flat:       "
        f"{sum(t['pnl'] == 0 for t in recovery_trades)}"
    )

    print("\nEXIT RULES")

    exit_groups = defaultdict(list)

    for trade in trades:
        exit_groups[trade["exit_rule"]].append(
            trade["pnl"]
        )

    for rule, values in sorted(exit_groups.items()):
        print(
            f"{rule:24} "
            f"trades={len(values):3d} "
            f"pnl=${sum(values):8.4f} "
            f"avg=${mean(values):7.4f}"
        )

    print("\nSYMBOL ATTRIBUTION")

    symbol_groups = defaultdict(list)

    for trade in trades:
        symbol_groups[trade["symbol"]].append(
            trade["pnl"]
        )

    ranked_symbols = sorted(
        symbol_groups.items(),
        key=lambda item: sum(item[1]),
        reverse=True,
    )

    for symbol, values in ranked_symbols:
        symbol_winners = sum(x > 0 for x in values)

        print(
            f"{symbol:8} "
            f"trades={len(values):3d} "
            f"win={symbol_winners / len(values) * 100:6.2f}% "
            f"pnl=${sum(values):8.4f} "
            f"avg=${mean(values):7.4f} "
            f"pf={profit_factor(values):6.3f}"
        )

    print("\nWORST 10 TRADES")

    worst = sorted(
        trades,
        key=lambda trade: trade["pnl"],
    )[:10]

    def fmt(value, decimals=4):
        if value is None:
            return "N/A"
        return f"{float(value):.{decimals}f}"

    for trade in worst:
        holding_seconds = trade["holding_seconds"]
        holding_minutes = (
            float(holding_seconds) / 60
            if holding_seconds is not None
            else None
        )

        print(
            f"id={trade['id']:4d} "
            f"{trade['symbol']:6} "
            f"pnl=${trade['pnl']:8.4f} "
            f"return_pct={fmt(trade['return_percent'], 2)} "
            f"entry_price={fmt(trade['entry_price'])} "
            f"exit_price={fmt(trade['exit_price'])} "
            f"entry_mean={fmt(trade['entry_mean'])} "
            f"exit_mean={fmt(trade['exit_mean'])} "
            f"entry_z={fmt(trade['entry_z'], 3)} "
            f"exit_z={fmt(trade['exit_z'], 3)} "
            f"holding_min={fmt(holding_minutes, 2)} "
            f"rule={trade['exit_rule']}"
        )


if __name__ == "__main__":
    main()
