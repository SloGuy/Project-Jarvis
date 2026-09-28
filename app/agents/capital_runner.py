"""Execute explicit Capital research tasks without engineering dispatch."""

import json
import threading

from app.agents import tasks
from app.agents.capital_recovery import MAX_EXECUTION_ATTEMPTS
from app.agents.capital_registry import RESEARCH_AGENT_ID
from app.capital.autonomy_control import read_operating_policy
from app.capital.autonomy_policy import authorize_capital_action
from app.capital.autonomy_research_draft import (
    apply_revision_draft,
    prepare_revision_draft,
)


HEARTBEAT_SECONDS = 15


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate task field.")
        result[key] = value
    return result


def _instruction(task):
    if task.assigned_agent_id != RESEARCH_AGENT_ID:
        raise ValueError("Task must belong to the Capital research agent.")
    if task.status != tasks.TaskStatus.QUEUED:
        raise ValueError("Only queued tasks can enter the Capital runner.")

    value = json.loads(task.objective, object_pairs_hook=_unique_keys)
    expected = {"action", "parent_research_id", "objective"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Invalid Capital research task structure.")
    if value["action"] != "capital.revise_research":
        raise ValueError("Unsupported Capital research action.")

    for field, maximum in (("parent_research_id", 160), ("objective", 2000)):
        text = value[field]
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > maximum
        ):
            raise ValueError(f"Invalid task {field}.")
    return value


def _authorize():
    authorize_capital_action(
        agent_id=RESEARCH_AGENT_ID,
        action="capital.revise_research",
        policy=read_operating_policy(),
        execution_mode="paper",
    )


def _owned_task(state, claimed):
    record = state["tasks"].get(claimed.task_id)
    if record is None:
        raise ValueError("Claimed task disappeared.")
    current = tasks._task_from_record(record)
    if (
        current.status != tasks.TaskStatus.RUNNING
        or current.assigned_agent_id != RESEARCH_AGENT_ID
        or current.execution_attempts != claimed.execution_attempts
        or current.objective != claimed.objective
    ):
        raise ValueError("Capital task ownership changed.")
    return current


def run_research_task(task):
    """Run one revision task with durable drafts and attempt checks.

    Applying the revision holds the task lock briefly so task recovery
    cannot replace ownership during the research-state mutation.
    Model generation occurs outside that lock.

    A revision saved before completion can be recovered through its
    request receipt. This runner never repeats the model call for an
    already persisted draft.
    """
    _instruction(task)
    _authorize()
    claimed = tasks.claim_task(
        task_id=task.task_id,
        agent_id=RESEARCH_AGENT_ID,
        maximum_execution_attempts=MAX_EXECUTION_ATTEMPTS,
    )

    stop = threading.Event()
    heartbeat_errors = []

    def heartbeat():
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                tasks.heartbeat_task(
                    task_id=claimed.task_id,
                    agent_id=RESEARCH_AGENT_ID,
                    expected_execution_attempt=claimed.execution_attempts,
                )
            except Exception as error:
                heartbeat_errors.append(error)
                stop.set()
                return

    worker = threading.Thread(
        target=heartbeat,
        name="capital-task-heartbeat",
        daemon=True,
    )

    try:
        # Validate the persisted task returned by claim, not merely the
        # caller's potentially outdated queued object.
        persisted = tasks._task_from_record({
            **tasks._task_to_record(claimed),
            "status": tasks.TaskStatus.QUEUED.value,
        })
        instruction = _instruction(persisted)
        worker.start()

        prepare_revision_draft(
            task_id=claimed.task_id,
            parent_research_id=instruction["parent_research_id"],
            objective=instruction["objective"],
        )

        if heartbeat_errors:
            raise RuntimeError("Capital task heartbeat failed.")

        with tasks._state_lock():
            state = tasks._load_state_unlocked()
            _owned_task(state, claimed)
            if heartbeat_errors:
                raise RuntimeError("Capital task heartbeat failed.")
            _authorize()
            result = apply_revision_draft(task_id=claimed.task_id)

        # Stop heartbeat updates before transitioning out of RUNNING.
        stop.set()
        worker.join()
        if heartbeat_errors:
            raise RuntimeError("Capital task heartbeat failed.")

        return tasks.complete_task(
            task_id=claimed.task_id,
            result=json.dumps(result, sort_keys=True, allow_nan=False),
            expected_execution_attempt=claimed.execution_attempts,
        )

    except Exception as error:
        stop.set()
        if worker.ident is not None:
            worker.join()
        try:
            tasks.fail_task(
                task_id=claimed.task_id,
                error=f"{type(error).__name__}: Capital research task failed.",
                expected_execution_attempt=claimed.execution_attempts,
            )
        except ValueError:
            # Recovery or another state transition already replaced this
            # attempt. Never overwrite the newer task state.
            pass
        raise

    finally:
        stop.set()
        if worker.ident is not None:
            worker.join()
