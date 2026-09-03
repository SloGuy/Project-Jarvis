import json

from app.capital.volatility_breakout_paper_runner import (
    run_volatility_breakout_paper_cycle,
)


def main() -> None:
    result = run_volatility_breakout_paper_cycle()

    print(
        json.dumps(
            result,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
