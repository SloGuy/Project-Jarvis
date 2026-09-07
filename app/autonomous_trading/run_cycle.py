import json

from app.autonomous_trading.execution_recovery import (
    reconcile_pending_executions,
)
from app.autonomous_trading.strategy_runner import (
    run_momentum_strategy_cycle,
)
from app.capital.cycle_recorder import record_cycle


def main() -> None:
    with record_cycle(
        experiment_id="momentum_alignment_v1_paper_2026",
    ) as cycle:
        recovery_results = reconcile_pending_executions()

        cycle["recovery"] = {
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

        strategy_result = run_momentum_strategy_cycle()
        strategy_result["recovery"] = cycle["recovery"]
        cycle.update(strategy_result)

    print(
        json.dumps(
            strategy_result,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
