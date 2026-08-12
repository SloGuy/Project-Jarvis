import json

from app.autonomous_trading.execution_recovery import (
    reconcile_pending_executions,
)
from app.autonomous_trading.strategy_runner import (
    run_momentum_strategy_cycle,
)


def main() -> None:
    recovery_results = reconcile_pending_executions()

    strategy_result = run_momentum_strategy_cycle()

    strategy_result["recovery"] = {
        "checked": len(recovery_results),
        "reconciled": sum(
            1
            for result in recovery_results
            if result.status == "reconciled"
        ),
        "still_executing": sum(
            1
            for result in recovery_results
            if result.status == "still_executing"
        ),
        "results": [
            {
                "decision_id": result.decision_id,
                "status": result.status,
                "transaction_id": result.transaction_id,
                "message": result.message,
            }
            for result in recovery_results
        ],
    }

    print(
        json.dumps(
            strategy_result,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
