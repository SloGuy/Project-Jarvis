"""Run serialized evaluation cycles for managed paper portfolios.

Lifecycle and operating controls are enforced inside accounting
transactions. This worker does not create or promote experiments.
"""

import fcntl
import json
from pathlib import Path

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.capital.paper_lifecycle_store import PaperLifecycleRecord
from app.capital.mean_reversion_v2_paper_runner import (
    run_mean_reversion_v2_paper_cycle,
)


DIRECTORY = (
    Path(__file__).resolve().parents[2]
    / "runtime" / "capital" / "autonomy"
)


def run_paper_cycle(*, directory=None):
    root = Path(directory) if directory is not None else DIRECTORY
    root.mkdir(parents=True, exist_ok=True)

    with (root / "paper-worker.lock").open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {
                "status": "busy",
                "portfolios": [],
                "live_capital_authorized": False,
            }

        with SessionLocal() as session:
            portfolio_ids = list(session.scalars(
                select(PaperLifecycleRecord.portfolio_id)
                .where(PaperLifecycleRecord.status.in_(
                    ("active", "paused", "demoted")
                ))
                .order_by(PaperLifecycleRecord.portfolio_id)
            ))

        outcomes = []
        for portfolio_id in portfolio_ids:
            try:
                # Full evaluation preserves fixed-target and timeout exits.
                # The accounting guard blocks entries when paused/disabled.
                report = run_mean_reversion_v2_paper_cycle(
                    portfolio_id=portfolio_id,
                    risk_only=False,
                )
                failed = report["execution_failed_count"]
                outcomes.append({
                    "portfolio_id": portfolio_id,
                    "status": "execution_failed" if failed else "evaluated",
                    "executed_count": report["executed_count"],
                    "execution_failed_count": failed,
                    "report": report,
                })
            except Exception as error:
                # A broken portfolio must not prevent checks of the others.
                outcomes.append({
                    "portfolio_id": portfolio_id,
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "reason": str(error)[:2000],
                })

        return {
            "status": (
                "partial_failure"
                if any(row["status"] in {"failed", "execution_failed"}
                       for row in outcomes)
                else "completed" if outcomes else "idle"
            ),
            "portfolios": outcomes,
            "live_capital_authorized": False,
        }


def main():
    try:
        result = run_paper_cycle()
    except Exception as error:
        result = {
            "status": "failed",
            "error_type": type(error).__name__,
            "reason": str(error)[:2000],
            "live_capital_authorized": False,
        }
    print(json.dumps(result, indent=2))
    if result["status"] in {"failed", "partial_failure"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
