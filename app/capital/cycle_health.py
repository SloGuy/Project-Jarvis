from datetime import datetime, timezone

from sqlalchemy import select

from app.capital.cycle_models import ExperimentCycle
from app.market_db.database import SessionLocal


def _serialize(row):
    if row is None:
        return None

    return {
        "id": row.id,
        "status": row.status,
        "started_at": row.started_at.isoformat(),
        "finished_at": (
            row.finished_at.isoformat()
            if row.finished_at is not None else None
        ),
        "error": row.error,
        "summary": row.summary,
    }


def get_cycle_health(*, experiment_id, mode="regular"):
    overdue_after = 180 if mode == "risk_only" else 900
    now = datetime.now(timezone.utc)

    with SessionLocal() as session:
        query = select(ExperimentCycle).where(
            ExperimentCycle.experiment_id == experiment_id,
            ExperimentCycle.mode == mode,
        )
        latest = session.scalar(
            query.order_by(
                ExperimentCycle.started_at.desc()
            ).limit(1)
        )
        completed = session.scalar(
            query.where(
                ExperimentCycle.status == "completed"
            ).order_by(
                ExperimentCycle.finished_at.desc()
            ).limit(1)
        )
        failed = session.scalar(
            query.where(
                ExperimentCycle.status.in_(
                    ["failed", "interrupted"]
                )
            ).order_by(
                ExperimentCycle.started_at.desc()
            ).limit(1)
        )

        age = None
        overdue = False
        failed_executions = 0
        unexecuted_exits = 0
        state = "no_recorded_cycle"

        if latest is not None:
            age = max(
                0, int((now - latest.started_at).total_seconds())
            )
            overdue = age > overdue_after
            summary = latest.summary or {}
            results = summary.get("results") or []

            failed_executions = max(
                int(summary.get("execution_failed_count") or 0),
                sum(
                    row.get("execution_status") == "failed"
                    for row in results
                ),
            )
            unexecuted_exits = sum(
                str(row.get("action", "")).lower() == "sell"
                and row.get("execution_status") != "executed"
                for row in results
            )

            state = latest.status
            if state == "completed":
                if failed_executions or unexecuted_exits:
                    state = "execution_problems"
            if overdue:
                state = "overdue"

        return {
            "mode": mode,
            "state": state,
            "overdue": overdue,
            "age_seconds": age,
            "overdue_after_seconds": overdue_after,
            "failed_execution_count": failed_executions,
            "unexecuted_exit_count": unexecuted_exits,
            "latest": _serialize(latest),
            "last_completed": _serialize(completed),
            "last_failed": _serialize(failed),
        }
