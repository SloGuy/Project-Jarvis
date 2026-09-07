import json

from app.capital.cycle_recorder import record_cycle
from app.capital.volatility_breakout_paper_runner import (
    run_volatility_breakout_paper_cycle,
)


def main() -> None:
    with record_cycle(
        experiment_id="volatility_breakout_v1_paper_2026",
    ) as cycle:
        result = run_volatility_breakout_paper_cycle()
        cycle.update(result)

    print(
        json.dumps(
            result,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
