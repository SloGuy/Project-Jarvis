"""Transaction-local guards for lifecycle-managed paper portfolios.

Call lock_managed_lifecycle before acquiring the portfolio row lock.
Hold both locks until the accounting transaction commits or rolls back.
"""

from decimal import Decimal

from sqlalchemy import select

from app.capital.autonomy_control import read_operating_policy
from app.capital.experiment_factory_packet import packet_digest
from app.capital.paper_lifecycle_store import PaperLifecycleRecord
from app.market_db.models import PortfolioPosition


def lock_managed_lifecycle(*, session, portfolio_id):
    """Return locked lifecycle state, or None for an unmanaged portfolio."""
    return session.scalar(
        select(PaperLifecycleRecord)
        .where(PaperLifecycleRecord.portfolio_id == portfolio_id)
        .with_for_update()
    )


def require_managed_operation(*, lifecycle, portfolio, operation):
    if lifecycle is None:
        return

    snapshot = lifecycle.authorization_snapshot
    experiment = snapshot["experiment"]
    packet = snapshot["packet"]
    if (
        lifecycle.portfolio_id != portfolio.id
        or lifecycle.execution_mode != "paper"
        or portfolio.portfolio_type != "paper"
        or experiment["portfolio_id"] != portfolio.id
        or experiment["portfolio_name"] != portfolio.name
        or packet["request"]["request_key"] != lifecycle.request_key
        or snapshot["authorization"]["basis"] != "operating_policy"
        or snapshot["authorization"]["live_capital_authorized"] is not False
    ):
        raise ValueError("Managed paper portfolio binding differs.")

    # Reject malformed/nonfinite JSON evidence before using its fields.
    packet_digest(packet)

    if operation not in {"buy", "sell"}:
        raise ValueError(
            "Managed portfolios cannot use direct cash changes or resets."
        )
    if lifecycle.status not in {"active", "paused", "demoted"}:
        raise ValueError("Managed portfolio is not available for trading.")

    if operation == "buy":
        policy = read_operating_policy()
        if (
            not policy.enabled
            or policy.paused
            or policy.execution_mode != "paper"
            or lifecycle.status != "active"
        ):
            raise PermissionError("Managed paper entries are disabled.")

    # Selling existing holdings remains available during pause/demotion.
    # Ordinary accounting checks still prohibit selling more than is held.


def require_managed_buy(
    *,
    session,
    lifecycle,
    portfolio,
    asset,
    cash_required,
):
    if lifecycle is None:
        return

    require_managed_operation(
        lifecycle=lifecycle,
        portfolio=portfolio,
        operation="buy",
    )
    research = lifecycle.authorization_snapshot["packet"]["review"]["research"]
    if (
        research["strategy_name"] != "mean_reversion_v2"
        or research["asset_universe"] != ["BTC"]
        or asset.symbol != "BTC"
        or asset.asset_type != "crypto"
    ):
        raise ValueError("Purchase is outside the validated BTC universe.")

    allocation = Decimal(str(lifecycle.allocation_usd))
    cost = Decimal(str(cash_required))
    if (
        not allocation.is_finite()
        or not Decimal("0") < allocation <= Decimal("1000")
        or not cost.is_finite()
        or cost <= 0
    ):
        raise ValueError("Invalid managed allocation or purchase cost.")

    positions = session.scalars(
        select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio.id
        )
    ).all()
    committed_cost = Decimal("0")
    for position in positions:
        quantity = Decimal(str(position.quantity))
        basis = Decimal(str(position.average_cost_usd))
        if (
            not quantity.is_finite()
            or not basis.is_finite()
            or quantity < 0
            or basis < 0
        ):
            raise ValueError("Invalid managed position accounting.")
        if quantity == 0:
            continue
        if position.asset_id != asset.id:
            raise ValueError("Managed portfolio holds an unexpected asset.")
        committed_cost += quantity * basis

    # Acquisition-cost limit, including the new order's fees.
    # Market exposure and position sizing remain separate risk checks.
    if committed_cost + cost > allocation:
        raise ValueError("Purchase exceeds the managed allocation.")
