import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone

from app.capital.cycle_models import ExperimentCycle
from app.market_db.database import SessionLocal


logger = logging.getLogger(__name__)


def _start_cycle(experiment_id, mode):
    try:
        with SessionLocal() as session:
            record = ExperimentCycle(
                experiment_id=experiment_id,
                mode=mode,
            )
            session.add(record)
            session.commit()
            return record.id
    except Exception:
        logger.exception(
            "Cycle oversight could not record start: %s / %s",
            experiment_id,
            mode,
        )
        return None


def _finish_cycle(cycle_id, status, summary, error):
    if cycle_id is None:
        return

    try:
        with SessionLocal() as session:
            record = session.get(ExperimentCycle, cycle_id)
            if record is None:
                raise RuntimeError("Cycle record is missing.")

            record.status = status
            record.finished_at = datetime.now(timezone.utc)
            record.summary = json.loads(
                json.dumps(summary, default=str)
            )
            record.error = error
            session.commit()
    except Exception:
        logger.exception(
            "Cycle oversight could not record finish: %s",
            cycle_id,
        )


@contextmanager
def record_cycle(*, experiment_id, mode="regular"):
    if mode not in ("regular", "risk_only"):
        raise ValueError("Unsupported cycle mode.")

    cycle_id = _start_cycle(experiment_id, mode)
    summary = {}
    status = "interrupted"
    error = None

    try:
        yield summary
        status = "completed"
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"[:2000]
        raise
    finally:
        _finish_cycle(cycle_id, status, summary, error)
