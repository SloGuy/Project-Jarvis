from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.autonomous_trading.strategy import StrategyAction
from app.market_db.database import SessionLocal
from app.market_db.models import (
    AutonomousStrategyState,
    MarketAsset,
)


REQUIRED_CONFIRMATIONS = 3


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_datetime(
    value: datetime,
) -> datetime:
    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


@dataclass(frozen=True)
class SignalConfirmation:
    symbol: str
    strategy_name: str
    action: StrategyAction
    confirmation_count: int
    required_confirmations: int
    confirmed: bool
    observation_counted: bool


def update_signal_confirmation(
    *,
    symbol: str,
    strategy_name: str,
    action: StrategyAction,
    observation_at: datetime,
) -> SignalConfirmation:
    """
    Update persistent confirmation state for one strategy signal.

    Only a new market observation can increment confirmation count.

    HOLD resets pending BUY or SELL confirmation.

    This function does not create proposals or execute trades.
    """

    normalized_symbol = symbol.strip().upper()

    if not normalized_symbol:
        raise ValueError(
            "symbol must not be empty."
        )

    normalized_observation_at = _normalize_datetime(
        observation_at
    )

    now = utc_now()

    with SessionLocal() as session:
        # MR2 confirmations must belong to the current position lifecycle.
        if strategy_name == "mean_reversion_v2":
            from app.capital.experiment_registry import list_experiments
            from app.market_db.models import Portfolio, PortfolioTransaction

            experiments = [
                e for e in list_experiments()
                if e.strategy_name == strategy_name
            ]
            if len(experiments) != 1:
                raise ValueError("MR2 requires exactly one experiment.")

            portfolio = session.scalar(
                select(Portfolio).where(
                    Portfolio.name == experiments[0].portfolio_name,
                    Portfolio.is_active.is_(True),
                ).with_for_update()
            )
            if portfolio is None or portfolio.portfolio_type != "paper":
                raise ValueError("MR2 paper portfolio is unavailable.")


        asset = session.scalar(
            select(MarketAsset)
            .where(
                MarketAsset.symbol == normalized_symbol,
                MarketAsset.is_active.is_(True),
            )
            .order_by(MarketAsset.id)
            .limit(1)
        )

        if asset is None:
            raise ValueError(
                f"Active asset {normalized_symbol} was not found."
            )

        state = session.scalar(
            select(AutonomousStrategyState).where(
                AutonomousStrategyState.asset_id == asset.id,
                AutonomousStrategyState.strategy_name
                == strategy_name,
            )
        )

        if state is None:
            state = AutonomousStrategyState(
                asset_id=asset.id,
                strategy_name=strategy_name,
                pending_action=None,
                confirmation_count=0,
                first_confirmed_at=None,
                last_confirmed_at=None,
                last_observation_at=None,
                updated_at=now,
            )

            session.add(state)

        previous_observation_at = (
            _normalize_datetime(
                state.last_observation_at
            )
            if state.last_observation_at is not None
            else None
        )


        if strategy_name == "mean_reversion_v2":
            latest_fill = session.scalar(
                select(PortfolioTransaction)
                .where(
                    PortfolioTransaction.portfolio_id == portfolio.id,
                    PortfolioTransaction.asset_id == asset.id,
                    PortfolioTransaction.transaction_type.in_(("buy", "sell")),
                )
                .order_by(
                    PortfolioTransaction.created_at.desc(),
                    PortfolioTransaction.id.desc(),
                )
                .limit(1)
            )
            if latest_fill is not None:
                cutoff = _normalize_datetime(latest_fill.created_at)
                if previous_observation_at is None or previous_observation_at <= cutoff:
                    state.pending_action = None
                    state.confirmation_count = 0
                    state.first_confirmed_at = None
                    state.last_confirmed_at = None
                    state.last_observation_at = cutoff
                    state.updated_at = now
                    previous_observation_at = cutoff

                if normalized_observation_at <= cutoff:
                    session.commit()
                    return SignalConfirmation(
                        symbol=normalized_symbol,
                        strategy_name=strategy_name,
                        action=action,
                        confirmation_count=state.confirmation_count,
                        required_confirmations=REQUIRED_CONFIRMATIONS,
                        confirmed=False,
                        observation_counted=False,
                    )

        observation_counted = (
            previous_observation_at is None
            or normalized_observation_at
            > previous_observation_at
        )

        if not observation_counted:
            confirmed = (
                action != StrategyAction.HOLD
                and state.pending_action == action.value
                and state.confirmation_count
                >= REQUIRED_CONFIRMATIONS
            )

            return SignalConfirmation(
                symbol=normalized_symbol,
                strategy_name=strategy_name,
                action=action,
                confirmation_count=state.confirmation_count,
                required_confirmations=REQUIRED_CONFIRMATIONS,
                confirmed=confirmed,
                observation_counted=False,
            )

        state.last_observation_at = (
            normalized_observation_at
        )

        if action == StrategyAction.HOLD:
            state.pending_action = None
            state.confirmation_count = 0
            state.first_confirmed_at = None
            state.last_confirmed_at = None
            state.updated_at = now

        else:
            action_value = action.value

            if state.pending_action == action_value:
                state.confirmation_count += 1
                state.last_confirmed_at = now
                state.updated_at = now
            else:
                state.pending_action = action_value
                state.confirmation_count = 1
                state.first_confirmed_at = now
                state.last_confirmed_at = now
                state.updated_at = now

        session.commit()
        session.refresh(state)

        confirmed = (
            action != StrategyAction.HOLD
            and state.confirmation_count
            >= REQUIRED_CONFIRMATIONS
        )

        return SignalConfirmation(
            symbol=normalized_symbol,
            strategy_name=strategy_name,
            action=action,
            confirmation_count=state.confirmation_count,
            required_confirmations=REQUIRED_CONFIRMATIONS,
            confirmed=confirmed,
            observation_counted=True,
        )
