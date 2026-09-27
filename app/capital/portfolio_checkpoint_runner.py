"""Capture and persist one portfolio checkpoint under a process lock."""
import argparse
import fcntl
import json
from pathlib import Path
import sys

from app.capital.portfolio_checkpoint_capture import capture_portfolio_checkpoint
from app.capital.portfolio_checkpoint_store import save_portfolio_checkpoint


ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT_DIRECTORY = ROOT / "runtime/capital/portfolio_checkpoints"
LOCK_FILE = ROOT / "runtime/capital/portfolio_checkpoint.lock"


def run_once():
    """Return 0 for persisted evidence or 2 when another capture is running.

    Successful persistence does not imply complete valuations or eligible
    historical returns. Capture failures propagate to the CLI handler.
    """
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 2, {
                "status": "busy",
                "message": "Another portfolio checkpoint capture is running.",
                "checkpoint_persisted": False,
            }

        try:
            captured = capture_portfolio_checkpoint()
            saved = save_portfolio_checkpoint(
                directory=CHECKPOINT_DIRECTORY,
                checkpoint=captured,
            )
            return 0, {
                "status": "persisted",
                **saved,
                "sampled_at": captured["sampled_at"],
                "row_reconciliation": captured["row_reconciliation"]["status"],
                "accounting_effects": captured["accounting_effects"]["status"],
                "complete_indicative_count": (
                    captured["valuations"]["complete_indicative_count"]
                ),
                "incomplete_count": captured["valuations"]["incomplete_count"],
                "historical_return_eligible": False,
                "database_writes": False,
                "execution_authorized": False,
            }
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Persist one prospective portfolio evidence checkpoint."
    )
    parser.parse_args(argv)
    try:
        code, result = run_once()
    except Exception:
        print(json.dumps({
            "status": "failed",
            "message": (
                "Checkpoint capture or persistence failed. "
                "A file may already have been published; inspect before retrying."
            ),
            "historical_return_eligible": False,
            "database_writes": False,
            "execution_authorized": False,
        }), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
