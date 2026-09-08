"""Read-only, aligned trade-activity comparison of replay and paper journals."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, or_, text

from app.market_db.database import SessionLocal
from app.market_db.models import AutonomousTradeJournal
from app.capital.offline_verification import verify_report


def at(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compare_activity(report, paper):
    verification = verify_report(report)
    start, end = at(report["start"]), at(report["end_exclusive"])
    if (
        paper["strategy_name"] != "mean_reversion_v2"
        or at(paper["start"]) != start
        or at(paper["end_exclusive"]) != end
        or paper["asset_id"] != report["asset_id"]
        or paper["symbol"] != report["symbol"]
    ):
        raise ValueError("Paper slice and replay identity or period differ.")
    rows = paper["journals"]
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate paper journal IDs.")
    entries, exits, carry_in, carry_out, completed = [], [], [], [], []
    for row in rows:
        opened = at(row["opened_at"])
        closed = at(row["closed_at"]) if row["closed_at"] else None
        if closed is not None and closed < opened:
            raise ValueError("Paper journal closes before it opens.")
        if opened >= end or (closed is not None and closed < start):
            raise ValueError("Journal lies outside captured comparison scope.")
        if opened < start:
            carry_in.append(row["id"])
        else:
            entries.append(row)
        if closed is not None and start <= closed < end:
            exits.append(row)
            if opened >= start:
                completed.append(row)
        if closed is None or closed >= end:
            carry_out.append(row["id"])

    scenarios = {}
    for name, scenario in report["scenarios"].items():
        fills = [
            event for event in scenario["events"] if event["executed"]
        ]
        buys = [event for event in fills if event["fill"]["side"] == "buy"]
        sells = [event for event in fills if event["fill"]["side"] == "sell"]
        scenarios[name] = {
            "replay_entries": len(buys),
            "paper_entries": len(entries),
            "entry_count_difference": len(buys) - len(entries),
            "replay_exits": len(sells),
            "paper_exits": len(exits),
            "exit_count_difference": len(sells) - len(exits),
            "replay_realized_pnl": scenario["account"]["realized_pnl"],
            "replay_open_quantity": scenario["account"]["quantity"],
            "replay_fills": fills,
        }
    return {
        "schema_version": 1,
        "mode": "aligned_trade_activity_diagnostic",
        "start": report["start"], "end_exclusive": report["end_exclusive"],
        "asset_id": paper["asset_id"], "symbol": paper["symbol"],
        "strategy_name": paper["strategy_name"],
        "portfolio_id": paper["portfolio_id"],
        "verification": verification,
        "paper_entries": entries, "paper_exits": exits,
        "paper_carry_in_journal_ids": carry_in,
        "paper_carry_out_journal_ids": carry_out,
        "paper_completed_within_window": len(completed),
        "scenarios": scenarios,
        "initial_position_alignment": "mismatch" if carry_in else "both_flat_for_asset",
        "portfolio_return_comparable": False,
        "execution_parity_verified": False,
        "promotion_authorized": False,
        "limitations": [
            "Paper portfolio may hold other assets and have different cash and sizing.",
            "Replay starts flat; carried paper positions are explicitly identified.",
            "Journal entry/exit times are not a complete record of decision opportunities.",
            "Costs, live quotes, scheduling and original launch configuration may differ.",
            "Counts are aligned by asset and interval; trades are not assumed identical.",
            "Paper journal completeness is not independently reconciled to transactions.",
        ],
    }


def capture_paper(report, portfolio_id):
    if type(portfolio_id) is not int or portfolio_id <= 0:
        raise ValueError("A positive paper portfolio ID is required.")
    start, end = at(report["start"]), at(report["end_exclusive"])
    with SessionLocal() as session:
        session.execute(text(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        ))
        session.execute(text("SET LOCAL statement_timeout = '30s'"))
        rows = session.scalars(
            select(AutonomousTradeJournal).where(
                AutonomousTradeJournal.portfolio_id == portfolio_id,
                AutonomousTradeJournal.strategy_name == "mean_reversion_v2",
                AutonomousTradeJournal.asset_id == report["asset_id"],
                AutonomousTradeJournal.opened_at < end,
                or_(
                    AutonomousTradeJournal.closed_at.is_(None),
                    AutonomousTradeJournal.closed_at >= start,
                ),
            ).order_by(
                AutonomousTradeJournal.opened_at,
                AutonomousTradeJournal.id,
            ).limit(10001)
        ).all()
        if len(rows) > 10000:
            raise ValueError("Paper slice exceeds the bounded capture limit.")
        journals = [
            {
                "id": row.id,
                "opened_at": at(row.opened_at).isoformat(),
                "closed_at": at(row.closed_at).isoformat() if row.closed_at else None,
                "entry_quantity": str(row.entry_quantity),
                "entry_price_usd": str(row.entry_price_usd),
                "exit_price_usd": (
                    str(row.exit_price_usd) if row.exit_price_usd is not None else None
                ),
                "realized_gain_loss_usd": (
                    str(row.realized_gain_loss_usd)
                    if row.realized_gain_loss_usd is not None else None
                ),
                "exit_rule": row.exit_rule,
            }
            for row in rows
        ]
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "portfolio_id": portfolio_id, "strategy_name": "mean_reversion_v2",
        "asset_id": report["asset_id"], "symbol": report["symbol"],
        "start": report["start"], "end_exclusive": report["end_exclusive"],
        "journals": journals,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--portfolio-id", type=int, required=True)
    args = parser.parse_args()
    raw = args.report.read_bytes()
    report = json.loads(raw)
    verify_report(report)  # Reject invalid replay before accessing paper records.
    paper = capture_paper(report, args.portfolio_id)
    comparison = compare_activity(report, paper)
    comparison["input_report_sha256"] = hashlib.sha256(raw).hexdigest()
    output = Path(__file__).resolve().parents[2] / "work/paper_comparisons" / uuid4().hex
    output.mkdir(parents=True)
    for name, value in (("paper_slice.json", paper), ("comparison.json", comparison)):
        with (output / name).open("x") as handle:
            json.dump(value, handle, indent=2, default=str, allow_nan=False)
    print("Comparison:", output / "comparison.json")
    print("Paper entries:", len(comparison["paper_entries"]))
    print("Paper exits:", len(comparison["paper_exits"]))
    print("Carry-in positions:", len(comparison["paper_carry_in_journal_ids"]))
    print("Return parity: not established; activity comparison only.")


if __name__ == "__main__":
    main()
