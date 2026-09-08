"""Flat-position entry-signal replay; no orders or portfolio simulation."""
from datetime import timedelta
from decimal import Decimal

from app.autonomous_trading.strategy import PositionContext, StrategyAction
from app.autonomous_trading.signal_confirmation import (
    REQUIRED_CONFIRMATIONS, SignalConfirmation,
)
from app.autonomous_trading.mean_reversion_v2_strategy import (
    evaluate_mean_reversion_v2_strategy,
)
from app.capital.historical_observations import load_historical_snapshot


class ReplayConfirmation:
    def __init__(self):
        self.states = {}

    def update(self, *, symbol, strategy_name, action, observation_at):
        if observation_at.utcoffset() is None:
            raise ValueError("Observation timestamp must include timezone.")
        key = (symbol.strip().upper(), strategy_name)
        previous, pending, count = self.states.get(key, (None, None, 0))
        counted = previous is None or observation_at > previous
        if counted:
            if action == StrategyAction.HOLD:
                pending, count = None, 0
            elif pending == action.value:
                count += 1
            else:
                pending, count = action.value, 1
            self.states[key] = (observation_at, pending, count)
        return SignalConfirmation(
            symbol=key[0], strategy_name=strategy_name, action=action,
            confirmation_count=count,
            required_confirmations=REQUIRED_CONFIRMATIONS,
            confirmed=(
                action != StrategyAction.HOLD
                and pending == action.value
                and count >= REQUIRED_CONFIRMATIONS
            ),
            observation_counted=counted,
        )


def replay_entry_signals(
    session, *, asset_id, provider, start, end, step_seconds=300
):
    if start.utcoffset() is None or end.utcoffset() is None:
        raise ValueError("Replay timestamps must include timezones.")
    if end <= start:
        raise ValueError("end must be after start.")
    if type(step_seconds) is not int or step_seconds < 1:
        raise ValueError("step_seconds must be a positive integer.")
    if (end - start).total_seconds() / step_seconds > 10000:
        raise ValueError("Limit each replay to 10,000 decision times.")

    confirmation = ReplayConfirmation()
    records = []
    decision_at = start
    while decision_at < end:
        window = load_historical_snapshot(
            session, asset_id=asset_id, provider=provider,
            decision_at=decision_at,
        )
        snapshot = window["snapshot"]
        position = PositionContext(
            symbol=snapshot.symbol, quantity=Decimal("0"),
            average_cost_usd=Decimal("0"), market_value_usd=Decimal("0"),
            allocation_percent=Decimal("0"),
            unrealized_gain_loss_usd=Decimal("0"),
            unrealized_gain_loss_percent=Decimal("0"), opened_at=None,
        )
        candidate = evaluate_mean_reversion_v2_strategy(
            symbol=snapshot.symbol, position_context=position,
            snapshot=snapshot, confirmation_handler=confirmation.update,
        )
        records.append({
            "decision_at": decision_at.isoformat(),
            "observation_at": (
                snapshot.observation_at.isoformat()
                if snapshot.observation_at else None
            ),
            "observation_ids": window["observation_ids"],
            "usable": snapshot.usable,
            "unusable_reason": snapshot.reason,
            "z_score": str(snapshot.z_score) if snapshot.z_score is not None else None,
            "action": candidate.action.value,
            "rationale": candidate.rationale,
        })
        decision_at += timedelta(seconds=step_seconds)

    return {
        "mode": "flat_position_entry_signal_replay",
        "availability_verified": False,
        "schedule": "synthetic fixed interval; not recorded cycle timings",
        "asset_id": asset_id, "provider": provider,
        "start": start.isoformat(), "end_exclusive": end.isoformat(),
        "step_seconds": step_seconds,
        "decision_count": len(records),
        "unusable_count": sum(not row["usable"] for row in records),
        "buy_signal_count": sum(row["action"] == "buy" for row in records),
        "records": records,
    }
