"""Persist future validation drafts before registering them."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from app.agents.capital_registry import VALIDATION_AGENT_ID
from app.capital.autonomy_control import read_operating_policy
from app.capital.autonomy_policy import authorize_capital_action
from app.capital.autonomy_validation_plan import build_validation_draft
from app.capital.autonomy_validation_registration import (
    register_validation_once,
)
from app.capital.research_models import ResearchCandidate
from app.capital.research_store import locked_research_state, utc_now_iso
from app.capital.validation_plan import research_snapshot, timestamp
from app.capital import validation_registry as registry


def _authorize():
    authorize_capital_action(
        agent_id=VALIDATION_AGENT_ID,
        action="capital.register_validation",
        policy=read_operating_policy(),
        execution_mode="paper",
    )


def _identity(value, field):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 160
    ):
        raise ValueError(f"Invalid {field}.")
    return value.strip()


def _existing(state, task_id, research_id):
    drafts = state.get("autonomy_validation_drafts", {})
    if not isinstance(drafts, dict):
        raise RuntimeError("Invalid validation draft store.")
    saved = drafts.get(task_id)
    if saved is not None:
        if saved["research_id"] != research_id:
            raise ValueError("Task is already bound to another research candidate.")
        return deepcopy(saved)
    return None


def prepare_validation_draft(*, task_id, research_id):
    """Save a future window; do not register or start collection."""
    task_id = _identity(task_id, "task ID")
    research_id = _identity(research_id, "research ID")
    _authorize()

    with locked_research_state() as state:
        existing = _existing(state, task_id, research_id)
        if existing is not None:
            return existing

    now = datetime.now(timezone.utc)
    start = now.replace(second=0, microsecond=0) + timedelta(hours=1)
    draft = build_validation_draft(
        research_id=research_id,
        start=start.isoformat(),
        now=now,
    )

    # Preserve all reservations, including failed and completed plans.
    # Choose a window after existing reservations for this data series.
    with registry.locked_state() as state:
        ends = [
            timestamp(row["envelope"]["plan"]["end_exclusive"])
            for row in state["plans"].values()
            if (
                row["envelope"]["plan"]["asset_id"] == draft["asset_id"]
                and row["envelope"]["plan"]["provider"] == draft["provider"]
            )
        ]
    if ends and max(ends) > start:
        reserved_end = max(ends)
        start = reserved_end.replace(second=0, microsecond=0)
        if start < reserved_end:
            start += timedelta(minutes=1)
        draft = build_validation_draft(
            research_id=research_id,
            start=start.isoformat(),
            now=now,
        )

    _authorize()
    with locked_research_state(write=True) as state:
        existing = _existing(state, task_id, research_id)
        if existing is not None:
            return existing

        candidate = ResearchCandidate.from_dict(
            state["candidates"][research_id]
        )
        if research_snapshot(candidate) != draft["research"]:
            raise ValueError("Research changed while preparing validation.")

        saved = {
            "schema_version": 1,
            "task_id": task_id,
            "research_id": research_id,
            "created_at": utc_now_iso(),
            "draft": deepcopy(draft),
        }
        state.setdefault("autonomy_validation_drafts", {})[task_id] = saved

    return deepcopy(saved)


def register_saved_validation(*, task_id):
    """Register the saved draft once, preserving its original window."""
    task_id = _identity(task_id, "task ID")
    _authorize()

    with locked_research_state() as state:
        saved = deepcopy(
            state.get("autonomy_validation_drafts", {}).get(task_id)
        )
    if saved is None:
        raise KeyError("Saved validation draft not found.")
    if (
        saved["schema_version"] != 1
        or saved["task_id"] != task_id
        or saved["draft"]["research"]["research_id"] != saved["research_id"]
    ):
        raise ValueError("Invalid saved validation draft.")

    _authorize()
    return register_validation_once(
        saved["draft"],
        request_key=f"capital-validation:{task_id}",
    )
