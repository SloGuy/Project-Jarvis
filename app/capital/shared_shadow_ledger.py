"""Shared shadow accounting. No broker, database, or paper execution."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
import hashlib
import json

from app.capital.simulated_ledger import SimulatedLedger, MONEY, QUANTITY, number
from app.autonomous_trading.proposals import TradeProposal, TradeAction
from app.autonomous_trading.risk_governor import evaluate_trade_proposal


class SharedShadowLedger:
    def __init__(self, *, policy, strategy_caps, fee_bps, slippage_bps):
        self.policy = policy
        self.pricing = SimulatedLedger(
            policy.starting_capital_usd, fee_bps, slippage_bps
        )
        self.initial = self.pricing.starting_cash
        self.cash = self.initial
        self.caps = {
            name: number(cap) for name, cap in strategy_caps.items()
        }
        if not self.caps or any(
            not name.strip() or cap > 100 for name, cap in self.caps.items()
        ):
            raise ValueError("Explicit valid strategy caps are required.")
        self.positions = {}
        self.realized = {name: D("0") for name in self.caps}
        self.fees = {name: D("0") for name in self.caps}
        self.orders = {}
        self.last_decision = None

    def mark(self, prices):
        positions, attribution = {}, {}
        total_value = D("0")
        for (strategy, symbol), position in self.positions.items():
            price = number(prices[symbol], positive=True)
            value = (position["quantity"] * price).quantize(MONEY)
            total_value += value
            aggregate = positions.setdefault(symbol, {
                "symbol": symbol, "quantity": D("0"), "market_value_usd": D("0"),
            })
            aggregate["quantity"] += position["quantity"]
            aggregate["market_value_usd"] += value
            contribution = attribution.setdefault(strategy, {
                "market_value": D("0"), "unrealized_pnl": D("0"),
            })
            contribution["market_value"] += value
            contribution["unrealized_pnl"] += value - position["cost"]
        for strategy in self.caps:
            contribution = attribution.setdefault(strategy, {
                "market_value": D("0"), "unrealized_pnl": D("0"),
            })
            contribution.update(
                realized_pnl=self.realized[strategy], fees=self.fees[strategy]
            )
        equity = self.cash + total_value
        pnl = sum((
            item["realized_pnl"] + item["unrealized_pnl"]
            for item in attribution.values()
        ), D("0"))
        if equity - self.initial != pnl:
            raise ArithmeticError("Shared shadow accounting does not reconcile.")
        return {
            "status": "success", "total_value_usd": equity,
            "cash_balance_usd": self.cash, "market_value_usd": total_value,
            "position_count": len(positions),
            "positions": list(positions.values()),
            "strategy_attribution": attribution,
        }

    def execute(
        self, *, order_id, strategy, symbol, side, quantity,
        decision_at, quotes, confidence="100", rationale="Shadow simulation",
    ):
        if not isinstance(order_id, str) or not order_id.strip():
            raise ValueError("Order ID is required.")
        if strategy not in self.caps:
            raise ValueError("Strategy has no allocation cap.")
        symbol = symbol.strip().upper()
        if not symbol or side not in {"buy", "sell"}:
            raise ValueError("Invalid symbol or side.")
        if decision_at.utcoffset() is None:
            raise ValueError("Decision time must include timezone.")
        quantity = number(quantity, positive=True).quantize(QUANTITY)
        if quantity <= 0:
            raise ValueError("Quantity rounds to zero.")
        confidence = number(confidence)
        prices = {}
        normalized_quotes = {}
        for name, quote in quotes.items():
            price, at = quote
            price = number(price, positive=True)
            if at.utcoffset() is None or at >= decision_at:
                raise ValueError("Quotes must be timezone-aware and precede decisions.")
            prices[name] = price
            normalized_quotes[name] = [str(price), at.isoformat()]
        if symbol not in prices:
            raise ValueError("Order reference quote is missing.")

        fingerprint = hashlib.sha256(json.dumps({
            "strategy": strategy, "symbol": symbol, "side": side,
            "quantity": str(quantity), "decision_at": decision_at.isoformat(),
            "quotes": normalized_quotes, "confidence": str(confidence),
            "rationale": rationale,
        }, sort_keys=True).encode()).hexdigest()
        if order_id in self.orders:
            old = self.orders[order_id]
            if old["fingerprint"] != fingerprint:
                raise ValueError("Order ID was reused with different instructions.")
            return deepcopy(old["result"])
        if self.last_decision is not None and decision_at < self.last_decision:
            raise ValueError("Decision time cannot move backwards.")

        summary = self.mark(prices)
        key = (strategy, symbol)
        held = self.positions.get(key)
        reasons = []
        if side == "sell" and (held is None or quantity > held["quantity"]):
            reasons.append("Strategy cannot sell another strategy's holdings.")
        if side == "buy":
            for held_symbol in {symbol, *(key[1] for key in self.positions)}:
                age = (decision_at - quotes[held_symbol][1]).total_seconds()
                if age > self.policy.max_price_age_seconds:
                    reasons.append(f"Fresh valuation required for {held_symbol}.")

        fill = self.pricing.quote_fill(side, quantity, prices[symbol])
        proposal = replace(TradeProposal.create(
            symbol=symbol,
            action=TradeAction.BUY if side == "buy" else TradeAction.SELL,
            quantity=quantity, reference_price_usd=fill["fill_price"],
            price_observed_at=quotes[symbol][1],
            confidence_percent=confidence, rationale=rationale,
            strategy_name=strategy,
        ), created_at=decision_at)

        risk_summary = dict(summary)
        if side == "buy":
            adverse_cost = (
                fill["notional"] - (quantity * prices[symbol]).quantize(MONEY)
                + fill["fee"]
            )
            risk_summary["total_value_usd"] -= adverse_cost
            risk_summary["cash_balance_usd"] -= fill["fee"]
            allocated = summary["strategy_attribution"][strategy]["market_value"]
            equity = risk_summary["total_value_usd"]
            if equity <= 0 or (
                allocated + fill["notional"]
                > equity * self.caps[strategy] / 100
            ):
                reasons.append("Strategy allocation cap exceeded.")
            if fill["notional"] + fill["fee"] > self.cash:
                reasons.append("Shared cash is insufficient after costs.")
        decision = evaluate_trade_proposal(
            proposal=proposal, policy=self.policy,
            portfolio_summary=risk_summary, now=decision_at,
        )
        reasons.extend(decision.reasons)
        result = {
            "order_id": order_id, "strategy": strategy, "symbol": symbol,
            "side": side, "executed": not reasons,
            "reasons": reasons, "mode": "shadow",
            "paper_execution_authority": False, "live_capital_authority": False,
        }
        if not reasons:
            if side == "buy":
                debit = fill["notional"] + fill["fee"]
                self.cash -= debit
                if held is None:
                    held = {"quantity": D("0"), "cost": D("0")}
                    self.positions[key] = held
                held["quantity"] += quantity
                held["cost"] += debit
            else:
                cost = (
                    held["cost"] if quantity == held["quantity"] else
                    (held["cost"] * quantity / held["quantity"]).quantize(MONEY)
                )
                proceeds = fill["notional"] - fill["fee"]
                self.cash += proceeds
                self.realized[strategy] += proceeds - cost
                fill["realized_pnl"] = proceeds - cost
                held["quantity"] -= quantity
                held["cost"] -= cost
                if held["quantity"] == 0:
                    del self.positions[key]
            self.fees[strategy] += fill["fee"]
            result["fill"] = fill
        self.last_decision = decision_at
        result["account"] = self.mark(prices)
        self.orders[order_id] = {
            "fingerprint": fingerprint, "result": deepcopy(result),
        }
        return deepcopy(result)
