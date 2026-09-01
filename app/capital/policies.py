from dataclasses import replace

from app.autonomous_trading.policy import (
    INITIAL_1000_POLICY,
)


MEAN_REVERSION_1000_POLICY = replace(
    INITIAL_1000_POLICY,
    name="mean_reversion_1000",
    autonomous_execution_enabled=True,
)
