"""Deterministic two-strategy shadow coordination; no execution adapters."""
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal as D, ROUND_DOWN
import hashlib
import json

from app.autonomous_trading.strategy import PositionContext
from app.autonomous_trading.exit_rules import evaluate_exit_rules
from app.autonomous_trading.mean_reversion_v2_exit import (
    frozen_recovery_target, evaluate_fixed_exit,
)
from app.autonomous_trading.mean_reversion_v2_strategy import (
    evaluate_mean_reversion_v2_strategy,
)
from app.autonomous_trading.volatility_breakout_strategy import (
    evaluate_volatility_breakout_strategy,
)
from app.capital.signal_replay import ReplayConfirmation
from app.capital.shadow_risk import controls
from app.capital.simulated_ledger import QUANTITY
from app.capital.shared_shadow_ledger import SharedShadowLedger

EVALUATORS = {
    "mean_reversion_v2": evaluate_mean_reversion_v2_strategy,
    "volatility_breakout_v1": evaluate_volatility_breakout_strategy,
}



def allocation_quantity(ledger, *, strategy, price, prices, requested_percent):
    """Limit a requested entry to remaining strategy capacity after costs."""
    requested = D(str(requested_percent))
    if not requested.is_finite() or not 0 <= requested <= 100:
        raise ValueError("Requested position percent is invalid.")
    cap = ledger.caps[strategy] / 100
    if cap <= 0 or requested == 0:
        return D("0")
    account = ledger.mark(prices)
    equity = account["total_value_usd"]
    allocated = account["strategy_attribution"][strategy]["market_value"]
    if equity <= 0:
        return D("0")

    fill_price = ledger.pricing.quote_fill("buy", D("1"), price)["fill_price"]
    unit_debit = fill_price * (1 + ledger.pricing.fee_bps / 10000)
    # The ledger checks exposure against equity reduced by fees and slippage.
    # Reserve a few money quanta for notional and fee rounding.
    remaining = cap * equity - allocated - D("0.00000004")
    if remaining <= 0:
        return D("0")
    cap_quantity = remaining / (
        fill_price + cap * (unit_debit - price)
    )
    requested_quantity = equity * requested / 100 / price
    return min(requested_quantity, cap_quantity).quantize(
        QUANTITY, rounding=ROUND_DOWN
    )


class ShadowCoordinator:
    def __init__(self, *, policy, strategy_caps, fee_bps, slippage_bps):
        if set(strategy_caps) - set(EVALUATORS):
            raise ValueError("Unsupported shadow strategy.")
        self.ledger = SharedShadowLedger(
            policy=policy, strategy_caps=strategy_caps,
            fee_bps=fee_bps, slippage_bps=slippage_bps,
        )
        self.confirmations = {
            strategy: ReplayConfirmation() for strategy in strategy_caps
        }
        self.entry_state = {}
        self.last_tick = None
        self.last_fingerprint = None
        self.last_result = None

    def step(self, *, decision_at, snapshots, quotes, risk_only=False,
             risk_mode="normal", size_scales=None):
        size_scales = controls(self.confirmations, risk_mode, size_scales)
        if decision_at.utcoffset() is None or type(risk_only) is not bool:
            raise ValueError("Aware decision time and boolean risk mode required.")
        fingerprint = hashlib.sha256(json.dumps({
            "at": decision_at.isoformat(),
            "risk_only": risk_only,
            "risk_mode": risk_mode, "size_scales": size_scales,
            "snapshots": [
                [strategy, symbol, asdict(snapshot)]
                for (strategy, symbol), snapshot in sorted(snapshots.items())
            ],
            "quotes": sorted(quotes.items()),
        }, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()
        if self.last_tick is not None:
            if decision_at == self.last_tick:
                if fingerprint != self.last_fingerprint:
                    raise ValueError("Tick retry changed its inputs.")
                return deepcopy(self.last_result)
            if decision_at < self.last_tick:
                raise ValueError("Shadow ticks must increase.")
        # Commit the whole tick only if evaluation and accounting both succeed.
        trial = deepcopy(self)
        result = trial._step(
            decision_at=decision_at, snapshots=snapshots,
            quotes=quotes, risk_only=risk_only,
            risk_mode=risk_mode, size_scales=size_scales,
        )
        trial.last_tick = decision_at
        trial.last_fingerprint = fingerprint
        trial.last_result = deepcopy(result)
        self.__dict__.update(trial.__dict__)
        return deepcopy(result)

    def _step(self, *, decision_at, snapshots, quotes, risk_only,
              risk_mode, size_scales):
        for symbol, (price, observed) in quotes.items():
            if (
                not isinstance(price, D) or not price.is_finite() or price <= 0
                or observed.utcoffset() is None or observed >= decision_at
            ):
                raise ValueError(f"Invalid shadow quote: {symbol}")
        if set(self.ledger.positions) - set(snapshots):
            raise ValueError("Every held strategy position requires a snapshot.")
        prices = {symbol: quote[0] for symbol, quote in quotes.items()}
        account = self.ledger.mark(prices)
        intents, holds = [], []
        if risk_mode == "halted":
            return {
                "decision_at": decision_at.isoformat(), "risk_only": risk_only,
                "risk_mode": risk_mode, "size_scales": size_scales,
                "events": [], "holds": [{"reason": "Shadow orders are halted."}],
                "account": account, "mode": "shadow",
                "paper_execution_authority": False, "live_capital_authority": False,
            }
        for (strategy, symbol), snapshot in sorted(snapshots.items()):
            if strategy not in self.confirmations or snapshot.symbol != symbol:
                raise ValueError("Snapshot strategy or symbol mismatch.")
            if symbol not in quotes:
                raise ValueError("Snapshot reference quote is missing.")
            price, observed = quotes[symbol]
            if snapshot.latest_price_usd is not None and (
                snapshot.latest_price_usd != price
                or (
                    snapshot.observation_at is not None
                    and snapshot.observation_at != observed
                )
            ):
                raise ValueError("Snapshot and execution quote differ.")
            key = (strategy, symbol)
            position = self.ledger.positions.get(key)
            quantity = position["quantity"] if position else D("0")
            cost = position["cost"] if position else D("0")
            market = quantity * price
            state = self.entry_state.get(key)
            if position and state is None:
                raise ValueError("Held position is missing its strategy entry state.")
            context = PositionContext(
                symbol=symbol, quantity=quantity,
                average_cost_usd=cost / quantity if quantity else D("0"),
                market_value_usd=market,
                allocation_percent=(
                    market / account["total_value_usd"] * 100
                    if account["total_value_usd"] > 0 else D("0")
                ),
                unrealized_gain_loss_usd=market - cost,
                unrealized_gain_loss_percent=(
                    (market - cost) / cost * 100 if cost else D("0")
                ),
                opened_at=state["opened_at"] if state else None,
            )
            risk = evaluate_exit_rules(
                position_context=context, policy=self.ledger.policy,
                now=decision_at,
            )
            exit_rule = risk.rule.value if risk.should_exit else None
            if risk_only and not exit_rule:
                continue
            if position and strategy == "mean_reversion_v2" and not exit_rule:
                exit_rule = evaluate_fixed_exit(
                    current_price=price, recovery_target=state["target"],
                    opened_at=state["opened_at"], now=decision_at,
                )
            if exit_rule:
                side, confidence, rationale = "sell", D("100"), exit_rule
            else:
                candidate = EVALUATORS[strategy](
                    symbol=symbol, position_context=context, snapshot=snapshot,
                    confirmation_handler=self.confirmations[strategy].update,
                )
                side = candidate.action.value
                confidence, rationale = candidate.confidence_percent, candidate.rationale
                if side == "hold":
                    holds.append({"strategy": strategy, "symbol": symbol,
                                  "reason": rationale})
                    continue
                if side == "buy":
                    if position:
                        raise ValueError("Unexpected entry into an existing strategy lot.")
                    quantity = (
                        account["total_value_usd"]
                        * candidate.suggested_position_percent / 100 / price
                    ).quantize(QUANTITY, rounding=ROUND_DOWN)
                elif side != "sell":
                    raise ValueError("Unsupported strategy action.")
            if side == "buy" and risk_mode == "reduce_only":
                holds.append({"strategy": strategy, "symbol": symbol,
                              "reason": "Reduce-only mode blocks new entries."})
                continue
            if quantity <= 0:
                holds.append({"strategy": strategy, "symbol": symbol,
                              "reason": "Order quantity rounds to zero."})
                continue
            target = None
            if side == "buy" and strategy == "mean_reversion_v2":
                target = frozen_recovery_target(
                    entry_mean=snapshot.mean_price_usd,
                    entry_std=snapshot.standard_deviation_usd,
                )
            intents.append({
                "strategy": strategy, "symbol": symbol, "side": side,
                "quantity": quantity, "confidence": confidence,
                "rationale": rationale, "exit_rule": exit_rule, "target": target,
                "requested_percent": (
                    candidate.suggested_position_percent if side == "buy" else None
                ),
            })

        events = []
        for intent in sorted(
            intents, key=lambda item: (
                item["side"] != "sell", item["strategy"], item["symbol"]
            )
        ):
            strategy, symbol, side = (
                intent["strategy"], intent["symbol"], intent["side"]
            )
            if side == "buy":
                intent["quantity"] = allocation_quantity(
                    self.ledger, strategy=strategy, price=prices[symbol],
                    prices=prices, requested_percent=intent["requested_percent"],
                )
                intent["quantity"] = (
                    intent["quantity"] * size_scales[strategy]
                ).quantize(QUANTITY, rounding=ROUND_DOWN)
                if intent["quantity"] <= 0:
                    holds.append({
                        "strategy": strategy, "symbol": symbol,
                        "reason": "No remaining strategy allocation.",
                    })
                    continue
            order_id = hashlib.sha256(json.dumps([
                decision_at.isoformat(), strategy, symbol, side,
            ]).encode()).hexdigest()
            event = self.ledger.execute(
                order_id=order_id, strategy=strategy, symbol=symbol, side=side,
                quantity=intent["quantity"], decision_at=decision_at,
                quotes=quotes, confidence=intent["confidence"],
                rationale=intent["rationale"],
            )
            event["exit_rule"] = intent["exit_rule"]
            events.append(event)
            if event["executed"]:
                if side == "buy":
                    self.entry_state[(strategy, symbol)] = {
                        "opened_at": decision_at, "target": intent["target"],
                    }
                elif (strategy, symbol) not in self.ledger.positions:
                    self.entry_state.pop((strategy, symbol), None)
        return {
            "decision_at": decision_at.isoformat(), "risk_only": risk_only,
            "events": events, "holds": holds,
            "risk_mode": risk_mode, "size_scales": size_scales,
            "account": self.ledger.mark(prices),
            "mode": "shadow",
            "paper_execution_authority": False, "live_capital_authority": False,
        }
