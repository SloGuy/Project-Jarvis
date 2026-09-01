import json

from app.capital.mean_reversion_paper_runner import (
    run_mean_reversion_paper_cycle,
)


def main() -> None:
    result = run_mean_reversion_paper_cycle()

    print(
        json.dumps(
            result,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
