from collections import Counter, defaultdict
from decimal import Decimal

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import (
    AutonomousTradeDecision,
    AutonomousTradeJournal,
    PortfolioPosition,
    PortfolioTransaction,
)


def get_journal_health(*, portfolio_id):
    with SessionLocal() as session:
        session.connection(execution_options={
            "isolation_level": "REPEATABLE READ",
        })

        def load(model):
            return session.scalars(
                select(model).where(
                    model.portfolio_id == portfolio_id
                )
            ).all()

        journals = load(AutonomousTradeJournal)
        decisions = {
            row.id: row for row in load(AutonomousTradeDecision)
        }
        transactions = {
            row.id: row for row in load(PortfolioTransaction)
        }
        positions = load(PortfolioPosition)

        issues = []
        decision_links = Counter()
        transaction_links = Counter()
        open_quantities = defaultdict(Decimal)
        verified_closed = 0

        for journal in journals:
            before = len(issues)

            def flag(message):
                issues.append(f"Journal {journal.id}: {message}")

            if journal.status not in ("open", "closed"):
                flag("unknown status")

            sides = [("entry", "buy")]
            if journal.status == "closed":
                sides.append(("exit", "sell"))
                if not journal.exit_rule:
                    flag("missing exit rule")
                if journal.closed_at is None:
                    flag("missing close time")
                elif journal.closed_at < journal.opened_at:
                    flag("close time precedes entry")
                if journal.realized_gain_loss_usd is None:
                    flag("missing realized P&L")
            elif journal.status == "open":
                open_quantities[journal.asset_id] += (
                    journal.entry_quantity
                )
                if any(value is not None for value in (
                    journal.exit_decision_id,
                    journal.exit_transaction_id,
                    journal.closed_at,
                )):
                    flag("open journal contains exit links or close time")

            for side, action in sides:
                decision_id = getattr(journal, f"{side}_decision_id")
                transaction_id = getattr(
                    journal, f"{side}_transaction_id"
                )
                if decision_id is not None:
                    decision_links[decision_id] += 1
                if transaction_id is not None:
                    transaction_links[transaction_id] += 1

                decision = decisions.get(decision_id)
                transaction = transactions.get(transaction_id)
                if decision is None or transaction is None:
                    flag(f"{side} link missing or outside portfolio")
                    continue

                if (
                    decision.asset_id != journal.asset_id
                    or transaction.asset_id != journal.asset_id
                    or decision.action != action
                    or transaction.transaction_type != action
                    or decision.execution_status != "executed"
                    or decision.portfolio_transaction_id != transaction.id
                ):
                    flag(f"{side} decision/transaction mismatch")

                if (
                    transaction.quantity != journal.entry_quantity
                    or decision.quantity != transaction.quantity
                    or transaction.price_usd
                    != getattr(journal, f"{side}_price_usd")
                    or transaction.created_at
                    != getattr(
                        journal,
                        "opened_at" if side == "entry" else "closed_at",
                    )
                ):
                    flag(f"{side} quantity, price, or time mismatch")

                if side == "entry":
                    if decision.strategy_name != journal.strategy_name:
                        flag("entry strategy mismatch")

            if journal.status == "closed" and len(issues) == before:
                verified_closed += 1

        for label, links in (
            ("decision", decision_links),
            ("transaction", transaction_links),
        ):
            for linked_id, count in links.items():
                if count > 1:
                    issues.append(f"Duplicate {label} link: {linked_id}")

        for row in decisions.values():
            if (
                row.execution_status == "executed"
                and row.action in ("buy", "sell")
                and not decision_links[row.id]
            ):
                issues.append(f"Executed decision {row.id}: no journal link")

        for row in transactions.values():
            if (
                row.transaction_type in ("buy", "sell")
                and not transaction_links[row.id]
            ):
                issues.append(f"Trade transaction {row.id}: no journal link")

        held_quantities = defaultdict(Decimal)
        for position in positions:
            held_quantities[position.asset_id] += position.quantity

        for asset_id in set(open_quantities) | set(held_quantities):
            if open_quantities[asset_id] != held_quantities[asset_id]:
                issues.append(
                    f"Asset {asset_id}: position/open-journal quantity mismatch"
                )

        return {
            "state": "needs_review" if issues else "consistent",
            "portfolio_id": portfolio_id,
            "open_count": sum(j.status == "open" for j in journals),
            "closed_count": sum(j.status == "closed" for j in journals),
            "linked_closed_count": verified_closed,
            "issue_count": len(issues),
            "issues": issues[:20],
            "issues_truncated": len(issues) > 20,
        }
