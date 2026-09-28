"""Consistent ledger inputs for managed paper lifecycle decisions.

Cash and quantity reconciliation does not prove journal completeness,
market valuation accuracy, or administrator-resistant history.
"""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import (
    Portfolio,
    PortfolioPosition,
    PortfolioTransaction,
)
from app.capital.paper_lifecycle_store import PaperLifecycleRecord


def number(value):
    if isinstance(value, bool):
        raise ValueError("Invalid accounting number.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("Invalid accounting number.") from error
    if not result.is_finite():
        raise ValueError("Nonfinite accounting number.")
    return result


def read_paper_accounting(request_key):
    with SessionLocal() as session:
        with session.begin():
            lifecycle = session.scalar(
                select(PaperLifecycleRecord)
                .where(PaperLifecycleRecord.request_key == request_key)
                .with_for_update()
            )
            if lifecycle is None:
                raise KeyError("Lifecycle record not found.")
            portfolio = session.scalar(
                select(Portfolio)
                .where(Portfolio.id == lifecycle.portfolio_id)
                .with_for_update()
            )
            if portfolio is None or portfolio.portfolio_type != "paper":
                raise ValueError("Paper portfolio binding is unavailable.")

            transactions = session.scalars(
                select(PortfolioTransaction)
                .where(PortfolioTransaction.portfolio_id == portfolio.id)
                .order_by(PortfolioTransaction.id)
            ).all()
            positions = session.scalars(
                select(PortfolioPosition)
                .where(PortfolioPosition.portfolio_id == portfolio.id)
            ).all()

            snapshot = lifecycle.authorization_snapshot
            initial = number(
                snapshot["experiment"]["starting_capital_usd"]
            )
            if initial != Decimal("1000"):
                raise ValueError("Unsupported initial paper capital.")
            expected_cash = initial
            expected_quantities = defaultdict(lambda: Decimal("0"))
            realized = Decimal("0")
            sells = 0
            issues = []

            for row in transactions:
                if row.transaction_type not in {"buy", "sell"}:
                    issues.append(f"Unsupported transaction: {row.id}")
                    continue
                try:
                    quantity = number(row.quantity)
                    total = number(row.total_usd)
                    fees = number(row.fees_usd)
                    if (
                        row.asset_id is None
                        or quantity <= 0
                        or total < 0
                        or fees < 0
                    ):
                        raise ValueError("Invalid trade amounts.")
                    if row.transaction_type == "buy":
                        expected_cash -= total + fees
                        expected_quantities[row.asset_id] += quantity
                    else:
                        if fees > total:
                            raise ValueError("Sale fees exceed proceeds.")
                        gain = number(row.realized_gain_loss_usd)
                        expected_cash += total - fees
                        expected_quantities[row.asset_id] -= quantity
                        realized += gain
                        sells += 1
                except ValueError:
                    issues.append(f"Invalid accounting fields: {row.id}")

            actual_quantities = {}
            holding_count = 0
            for row in positions:
                try:
                    quantity = number(row.quantity)
                    if quantity < 0 or row.asset_id in actual_quantities:
                        raise ValueError("Invalid or duplicate position.")
                    actual_quantities[row.asset_id] = quantity
                    holding_count += int(quantity > 0)
                except ValueError:
                    issues.append(f"Invalid position: {row.id}")

            try:
                cash = number(portfolio.cash_balance_usd)
                if cash < 0 or cash != expected_cash:
                    issues.append("Cash does not match the trade ledger.")
            except ValueError:
                issues.append("Invalid portfolio cash.")

            for asset_id in set(expected_quantities) | set(actual_quantities):
                expected = expected_quantities[asset_id]
                if expected < 0 or expected != actual_quantities.get(
                    asset_id, Decimal("0")
                ):
                    issues.append(f"Quantity mismatch for asset {asset_id}.")

            now = datetime.now(timezone.utc)
            active_times = []
            for event in lifecycle.transition_history:
                if event.get("to") != "active":
                    continue
                started = datetime.fromisoformat(event["at"])
                if started.utcoffset() is None or started > now:
                    raise ValueError("Invalid activation timestamp.")
                active_times.append(started)
            if lifecycle.status == "active" and not active_times:
                issues.append("Active experiment has no activation receipt.")
            age = (
                int((now - min(active_times)).total_seconds())
                if active_times else 0
            )

            return {
                "request_key": request_key,
                "research_id": snapshot["experiment"]["research_id"],
                "portfolio_id": portfolio.id,
                "status": lifecycle.status,
                "version": lifecycle.version,
                "sampled_at": now.isoformat(),
                "accounting_valid": not issues,
                "issues": issues,
                "realized_gain_loss_usd": str(realized),
                "sell_fill_count": sells,
                "holding_count": holding_count,
                "active_age_seconds": age,
                "transaction_count": len(transactions),
                "scope": "ledger_cash_and_quantity_reconciliation",
                "historical_completeness_verified": False,
                "database_writes": False,
            }
