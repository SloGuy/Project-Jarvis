"""Run one quote provenance cycle with a nonblocking process lock."""

import argparse
import fcntl
import json
from pathlib import Path
import sys

from app.capital.quote_provenance_cycle import collect_held_quote_provenance


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCK_FILE = (
    PROJECT_ROOT / "runtime" / "capital" / "quote_provenance_cycle.lock"
)

# 0: all captures completed, or no held assets
# 1: failed or partial capture
# 2: another cycle already holds the lock
# Successful capture does not imply timestamp eligibility.


def run_once():
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    with LOCK_FILE.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(
                handle.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            return 2, {
                "status": "busy",
                "message": "Another provenance collection cycle is running.",
            }

        try:
            result = collect_held_quote_provenance()
            status = result.get("status")
            if status not in {"captured", "empty", "partial", "failed"}:
                raise ValueError("Unexpected collection status.")
            return (0 if status in {"captured", "empty"} else 1), result
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run one non-overlapping held-asset provenance cycle."
    )
    parser.parse_args(argv)

    try:
        code, result = run_once()
    except Exception:
        # Request exceptions can contain credential-bearing URLs.
        print(
            json.dumps({
                "status": "failed",
                "message": (
                    "Provenance cycle failed. Raw error details are suppressed. "
                    "Some records may already have been published."
                ),
            }),
            file=sys.stderr,
        )
        return 1

    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
