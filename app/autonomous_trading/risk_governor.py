from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.autonomous_trading.asset_risk_metadata import (
    get_asset_risk_metadata,
)

from app.autonomous_trading.policy import RiskPolicy
from app.autonomous_trading.proposals import (
    TradeAction,
    TradeProposal,
)


ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_decimal(value: object) -> Decimal:
    if value is None:
        return ZERO

    return Decimal(str(value))


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    policy_name: str
    symbol: str
    action: TradeAction
    reasons: tuple[str, ...]
    evaluated_at: datetime


def evaluate_trade_proposal(
    *,
    proposal: TradeProposal,
    policy: RiskPolicy,
    portfolio_summary: dict,
    now: datetime | None = None,
) -> RiskDecision:
    """
    Evaluate a trade proposal against a risk policy and portfolio state.

    This function performs risk evaluation only.
    It does not execute trades or import the paper trading engine.
    """

    reasons: list[str] = []

    evaluated_at = _normalize_datetime(
        now if now is not None else utc_now()
    )

    if portfolio_summary.get("status") != "success":
        reasons.append(
            "Portfolio state is unavailable."
        )

    total_value = _to_decimal(
        portfolio_summary.get("total_value_usd")
    )
    cash_balance = _to_decimal(
        portfolio_summary.get("cash_balance_usd")
    )
    current_market_value = _to_decimal(
        portfolio_summary.get("market_value_usd")
    )

    position_count = int(
        portfolio_summary.get("position_count") or 0
    )

    if total_value <= ZERO:
        reasons.append(
            "Portfolio total value must be greater than zero."
        )

    if proposal.quantity <= ZERO:
        reasons.append(
            "Trade quantity must be greater than zero."
        )

    if proposal.reference_price_usd <= ZERO:
        reasons.append(
            "Reference price must be greater than zero."
        )

    if policy.require_rationale and not proposal.rationale:
        reasons.append(
            "A trade rationale is required."
        )

    if policy.require_confidence:
        if proposal.confidence_percent < ZERO:
            reasons.append(
                "Confidence cannot be negative."
            )
        elif proposal.confidence_percent > ONE_HUNDRED:
            reasons.append(
                "Confidence cannot exceed 100 percent."
            )
        elif (
            proposal.confidence_percent
            < policy.minimum_confidence_percent
        ):
            reasons.append(
                "Proposal confidence is below the policy minimum."
            )

    observed_at = _normalize_datetime(
        proposal.price_observed_at
    )

    price_age_seconds = (
        evaluated_at - observed_at
    ).total_seconds()

    if price_age_seconds < 0:
        reasons.append(
            "Reference price timestamp is in the future."
        )
    elif price_age_seconds > policy.max_price_age_seconds:
        reasons.append(
            "Reference price is stale."
        )

    proposed_trade_value = (
        proposal.quantity
        * proposal.reference_price_usd
    )

    positions = portfolio_summary.get("positions") or []

    proposal_metadata = get_asset_risk_metadata(
        proposal.symbol
    )

    existing_position = next(
        (
            position
            for position in positions
            if str(position.get("symbol", "")).upper()
            == proposal.symbol.upper()
        ),
        None,
    )

    if proposal.action == TradeAction.BUY:
        if total_value > ZERO:
            existing_position_value = ZERO

            if existing_position is not None:
                existing_position_value = _to_decimal(
                    existing_position.get("market_value_usd")
                )

            projected_position_value = (
                existing_position_value
                + proposed_trade_value
            )

            projected_position_percent = (
                projected_position_value
                / total_value
                * ONE_HUNDRED
            )

            if (
                projected_position_percent
                > policy.max_position_percent
            ):
                reasons.append(
                    "Projected position size exceeds "
                    "the policy maximum."
                )

            projected_total_exposure = (
                current_market_value
                + proposed_trade_value
            )

            projected_exposure_percent = (
                projected_total_exposure
                / total_value
                * ONE_HUNDRED
            )

            if (
                projected_exposure_percent
                > policy.max_total_exposure_percent
            ):
                reasons.append(
                    "Projected total exposure exceeds "
                    "the policy maximum."
                )

            if proposal_metadata is not None:
                current_sector_value = ZERO
                current_correlation_value = ZERO

                for position in positions:
                    position_symbol = str(
                        position.get("symbol", "")
                    ).upper()

                    position_metadata = (
                        get_asset_risk_metadata(
                            position_symbol
                        )
                    )

                    if position_metadata is None:
                        continue

                    position_value = _to_decimal(
                        position.get(
                            "market_value_usd"
                        )
                    )

                    if (
                        position_metadata.sector
                        == proposal_metadata.sector
                    ):
                        current_sector_value += (
                            position_value
                        )

                    if (
                        position_metadata.correlation_group
                        == proposal_metadata.correlation_group
                    ):
                        current_correlation_value += (
                            position_value
                        )

                projected_sector_value = (
                    current_sector_value
                    + proposed_trade_value
                )

                projected_sector_percent = (
                    projected_sector_value
                    / total_value
                    * ONE_HUNDRED
                )

                if (
                    projected_sector_percent
                    > policy.max_sector_exposure_percent
                ):
                    reasons.append(
                        "Projected sector exposure exceeds "
                        "the policy maximum."
                    )

                projected_correlation_value = (
                    current_correlation_value
                    + proposed_trade_value
                )

                projected_correlation_percent = (
                    projected_correlation_value
                    / total_value
                    * ONE_HUNDRED
                )

                if (
                    projected_correlation_percent
                    > policy.max_correlation_group_exposure_percent
                ):
                    reasons.append(
                        "Projected correlation-group exposure "
                        "exceeds the policy maximum."
                    )

            projected_cash = (
                cash_balance
                - proposed_trade_value
            )

            projected_cash_percent = (
                projected_cash
                / total_value
                * ONE_HUNDRED
            )

            if (
                projected_cash_percent
                < policy.minimum_cash_reserve_percent
            ):
                reasons.append(
                    "Projected cash reserve falls below "
                    "the policy minimum."
                )

        if proposed_trade_value > cash_balance:
            reasons.append(
                "Trade value exceeds available cash."
            )

        if (
            existing_position is None
            and position_count >= policy.max_open_positions
        ):
            reasons.append(
                "Maximum number of open positions has been reached."
            )

    elif proposal.action == TradeAction.SELL:
        if existing_position is None:
            reasons.append(
                "Cannot sell an asset that is not currently held."
            )
        else:
            held_quantity = _to_decimal(
                existing_position.get("quantity")
            )

            if proposal.quantity > held_quantity:
                reasons.append(
                    "Sell quantity exceeds the current position."
                )

    else:
        reasons.append(
            "Unsupported trade action."
        )

    return RiskDecision(
        approved=not reasons,
        policy_name=policy.name,
        symbol=proposal.symbol,
        action=proposal.action,
        reasons=tuple(reasons),
        evaluated_at=evaluated_at,
    )
