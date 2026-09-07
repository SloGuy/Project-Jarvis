import argparse
import fcntl
import json
from pathlib import Path

from app.capital.cycle_recorder import record_cycle
from app.capital.mean_reversion_v2_paper_runner import (
    run_mean_reversion_v2_paper_cycle,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--risk-only", action="store_true")
    args = parser.parse_args()

    lock_path = (
        Path.home() / ".jarvis-mean-reversion-v2-cycle.lock"
    )

    with lock_path.open("a") as lock:
        try:
            fcntl.flock(
                lock,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            print(json.dumps({
                "status": "skipped",
                "reason": "V2 cycle already running.",
            }))
            return

        with record_cycle(
            experiment_id="mean_reversion_v2_paper_2026",
            mode="risk_only" if args.risk_only else "regular",
        ) as cycle:
            result = run_mean_reversion_v2_paper_cycle(
                risk_only=args.risk_only,
            )
            cycle.update(result)

        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
