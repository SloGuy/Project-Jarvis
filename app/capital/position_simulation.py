"""Single-asset position simulation; no database-backed execution."""
from dataclasses import replace
from decimal import Decimal as D

from app.autonomous_trading.strategy import PositionContext
from app.autonomous_trading.proposals import TradeAction, TradeProposal
from app.autonomous_trading.risk_governor import evaluate_trade_proposal
from app.autonomous_trading.exit_rules import evaluate_exit_rules
from app.autonomous_trading.mean_reversion_v2_exit import (
    frozen_recovery_target, evaluate_fixed_exit,
)
from app.autonomous_trading.mean_reversion_v2_strategy import (
    evaluate_mean_reversion_v2_strategy,
)
from app.capital.signal_replay import ReplayConfirmation
from app.capital.simulated_ledger import SimulatedLedger, QUANTITY


class PositionSimulation:
    def __init__(self, *, symbol, policy, fee_bps, slippage_bps):
        self.symbol = symbol.strip().upper()
        if not self.symbol:
            raise ValueError("Symbol is required.")
        self.policy = policy
        self.ledger = SimulatedLedger(
            policy.starting_capital_usd, fee_bps, slippage_bps
        )
        self.confirmation = ReplayConfirmation()
        self.opened_at = None
        self.target = None
        self.last_decision = None
        self.events = []

    def step(self, snapshot, *, decision_at, risk_only=False):
        if decision_at.utcoffset() is None:
            raise ValueError("Decision time must include timezone.")
        if self.last_decision is not None and decision_at <= self.last_decision:
            raise ValueError("Decision times must strictly increase.")
        if snapshot.symbol != self.symbol:
            raise ValueError("Snapshot symbol mismatch.")
        if snapshot.observation_at is not None:
            if snapshot.observation_at.utcoffset() is None:
                raise ValueError("Observation time must include timezone.")
            if snapshot.observation_at >= decision_at:
                raise ValueError("Observation must precede decision.")
        self.last_decision = decision_at
        event = {
            "decision_at": decision_at.isoformat(),
            "risk_only": risk_only, "action": "hold",
            "executed": False, "reasons": [],
        }
        self.events.append(event)
        price = snapshot.latest_price_usd
        if price is None or snapshot.observation_at is None:
            event["reasons"] = ["No reference price available."]
            return event
        if not price.is_finite() or price <= 0:
            raise ValueError("Invalid reference price.")

        ledger = self.ledger
        mark = ledger.mark(price)
        held = ledger.quantity > 0
        average = ledger.entry_cost / ledger.quantity if held else D("0")
        context = PositionContext(
            symbol=self.symbol, quantity=ledger.quantity,
            average_cost_usd=average,
            market_value_usd=mark["market_value"],
            allocation_percent=(
                mark["market_value"] / mark["equity"] * 100
                if mark["equity"] > 0 else D("0")
            ),
            unrealized_gain_loss_usd=mark["unrealized_pnl"],
            unrealized_gain_loss_percent=(
                mark["unrealized_pnl"] / ledger.entry_cost * 100
                if held else D("0")
            ),
            opened_at=self.opened_at,
        )
        risk_exit = evaluate_exit_rules(
            position_context=context, policy=self.policy, now=decision_at
        )
        exit_rule = risk_exit.rule.value if risk_exit.should_exit else None
        if risk_only and not risk_exit.should_exit:
            return event
        if held and exit_rule is None:
            exit_rule = evaluate_fixed_exit(
                current_price=price, recovery_target=self.target,
                opened_at=self.opened_at, now=decision_at,
            )

        if exit_rule:
            action = TradeAction.SELL
            quantity = ledger.quantity
            confidence = D("100")
            rationale = exit_rule
        else:
            candidate = evaluate_mean_reversion_v2_strategy(
                symbol=self.symbol, position_context=context,
                snapshot=snapshot,
                confirmation_handler=self.confirmation.update,
            )
            if candidate.action.value != "buy":
                event["reasons"] = [candidate.rationale]
                return event
            action = TradeAction.BUY
            quantity = (
                mark["equity"] * candidate.suggested_position_percent
                / 100 / price
            ).quantize(QUANTITY)
            confidence = candidate.confidence_percent
            rationale = candidate.rationale

        event["action"] = action.value
        proposal = replace(
            TradeProposal.create(
                symbol=self.symbol, action=action, quantity=quantity,
                reference_price_usd=price,
                price_observed_at=snapshot.observation_at,
                confidence_percent=confidence, rationale=rationale,
                strategy_name="mean_reversion_v2",
            ),
            created_at=decision_at,
        )
        positions = [{
            "symbol": self.symbol, "quantity": ledger.quantity,
            "market_value_usd": mark["market_value"],
        }] if held else []
        summary = {
            "status": "success", "total_value_usd": mark["equity"],
            "cash_balance_usd": ledger.cash,
            "market_value_usd": mark["market_value"],
            "position_count": len(positions), "positions": positions,
        }
        decision = evaluate_trade_proposal(
            proposal=proposal, policy=self.policy,
            portfolio_summary=summary, now=decision_at,
        )
        if not decision.approved:
            event["reasons"] = list(decision.reasons)
            return event
        if not self.policy.autonomous_execution_enabled:
            event["reasons"] = ["Execution disabled by policy."]
            return event

        if action == TradeAction.BUY:
            target = frozen_recovery_target(
                entry_mean=snapshot.mean_price_usd,
                entry_std=snapshot.standard_deviation_usd,
            )
            preview = ledger.quote_fill("buy", quantity, price)
            # Recheck risk at the assumed execution price.
            execution_decision = evaluate_trade_proposal(
                proposal=replace(
                    proposal, reference_price_usd=preview["fill_price"]
                ),
                policy=self.policy, portfolio_summary=summary,
                now=decision_at,
            )
            remaining = ledger.cash - preview["notional"] - preview["fee"]
            reserve = mark["equity"] * self.policy.minimum_cash_reserve_percent / 100
            if not execution_decision.approved or remaining < reserve:
                event["reasons"] = list(execution_decision.reasons)
                if remaining < reserve:
                    event["reasons"].append("Costs breach simulated cash reserve.")
                return event
            fill = ledger.buy(quantity=quantity, reference_price=price)
            self.target, self.opened_at = target, decision_at
        else:
            fill = ledger.sell(reference_price=price)
            self.target, self.opened_at = None, None
        self.confirmation.reset_after_fill(
            symbol=self.symbol, strategy_name="mean_reversion_v2",
            filled_at=decision_at,
        )
        event.update(
            executed=True, fill=fill, exit_rule=exit_rule,
            account=ledger.mark(price),
        )
        return event
