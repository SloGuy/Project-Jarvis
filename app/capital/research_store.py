import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[2]

STATE_DIRECTORY = (
    PROJECT_ROOT
    / "runtime"
    / "capital"
)

RESEARCH_STATE_FILE = (
    STATE_DIRECTORY
    / "research_candidates.json"
)

RESEARCH_LOCK_FILE = (
    STATE_DIRECTORY
    / "research_candidates.lock"
)


def utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _default_state() -> dict:
    return {
        "candidates": {},
        "updated_at": None,
    }


def _load_state_unlocked() -> dict:
    try:
        with RESEARCH_STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as handle:
            payload = json.load(handle)

        if (
            isinstance(payload, dict)
            and isinstance(
                payload.get("candidates"),
                dict,
            )
        ):
            return payload

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        pass

    return _default_state()


def _save_state_unlocked(
    state: dict,
) -> None:
    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    state["updated_at"] = utc_now_iso()

    descriptor, temporary_path = (
        tempfile.mkstemp(
            prefix="research_",
            suffix=".json",
            dir=STATE_DIRECTORY,
        )
    )

    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                state,
                handle,
                indent=2,
                sort_keys=True,
            )
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(
            temporary_path,
            RESEARCH_STATE_FILE,
        )

    finally:
        if os.path.exists(
            temporary_path
        ):
            os.unlink(
                temporary_path
            )


@contextmanager
def locked_research_state(
    *,
    write: bool = False,
) -> Iterator[dict]:
    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    with RESEARCH_LOCK_FILE.open(
        "a+",
        encoding="utf-8",
    ) as lock_handle:
        lock_mode = (
            fcntl.LOCK_EX
            if write
            else fcntl.LOCK_SH
        )

        fcntl.flock(
            lock_handle.fileno(),
            lock_mode,
        )

        try:
            state = _load_state_unlocked()
            yield state

            if write:
                _save_state_unlocked(
                    state
                )

        finally:
            fcntl.flock(
                lock_handle.fileno(),
                fcntl.LOCK_UN,
            )


def get_research_state_snapshot() -> dict:
    with locked_research_state() as state:
        return {
            "candidates": dict(
                state["candidates"]
            ),
            "updated_at": state.get(
                "updated_at"
            ),
        }
