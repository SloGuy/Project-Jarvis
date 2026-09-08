"""Single-position simulation ledger. No database or broker access."""
from dataclasses import dataclass, field
from decimal import Decimal

D = Decimal
MONEY = D("0.00000001")
QUANTITY = D("0.000000000001")


def number(value, *, positive=False):
    result = D(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("Expected a finite nonnegative number.")
    if positive and result <= 0:
        raise ValueError("Expected a positive number.")
    return result


@dataclass
class SimulatedLedger:
    starting_cash: Decimal
    fee_bps: Decimal
    slippage_bps: Decimal
    cash: Decimal = field(init=False)
    quantity: Decimal = field(default=D("0"), init=False)
    entry_cost: Decimal = field(default=D("0"), init=False)
    realized_pnl: Decimal = field(default=D("0"), init=False)
    total_fees: Decimal = field(default=D("0"), init=False)
    fills: list = field(default_factory=list, init=False)

    def __post_init__(self):
        self.starting_cash = number(
            self.starting_cash, positive=True
        ).quantize(MONEY)
        if self.starting_cash <= 0:
            raise ValueError("Starting cash rounds to zero.")
        self.cash = self.starting_cash
        self.fee_bps = number(self.fee_bps)
        self.slippage_bps = number(self.slippage_bps)
        if self.fee_bps >= 10000 or self.slippage_bps >= 10000:
            raise ValueError("Cost assumptions must be below 10,000 bps.")

    def quote_fill(self, side, quantity, reference_price):
        if side not in {"buy", "sell"}:
            raise ValueError("Unsupported side.")
        qty = number(quantity, positive=True).quantize(QUANTITY)
        reference = number(reference_price, positive=True)
        if qty <= 0:
            raise ValueError("Quantity rounds to zero.")
        direction = D("1") if side == "buy" else D("-1")
        price = (
            reference * (1 + direction * self.slippage_bps / 10000)
        ).quantize(MONEY)
        if price <= 0:
            raise ValueError("Fill price rounds to zero.")
        notional = (qty * price).quantize(MONEY)
        if notional <= 0:
            raise ValueError("Trade value rounds to zero.")
        fee = (notional * self.fee_bps / 10000).quantize(MONEY)
        return {
            "side": side, "quantity": qty,
            "reference_price": reference, "fill_price": price,
            "notional": notional, "fee": fee,
        }

    def buy(self, *, quantity, reference_price):
        if self.quantity:
            raise ValueError("Position already open.")
        fill = self.quote_fill("buy", quantity, reference_price)
        debit = fill["notional"] + fill["fee"]
        if debit > self.cash:
            raise ValueError("Insufficient simulated cash after costs.")
        self.cash -= debit
        self.quantity = fill["quantity"]
        self.entry_cost = debit
        self.total_fees += fill["fee"]
        self.fills.append(dict(fill))
        return dict(fill)

    def sell(self, *, reference_price):
        if not self.quantity:
            raise ValueError("No simulated position to sell.")
        fill = self.quote_fill("sell", self.quantity, reference_price)
        proceeds = fill["notional"] - fill["fee"]
        if proceeds < 0:
            raise ValueError("Fees exceed sale proceeds.")
        pnl = proceeds - self.entry_cost
        self.cash += proceeds
        self.realized_pnl += pnl
        self.total_fees += fill["fee"]
        self.quantity = D("0")
        self.entry_cost = D("0")
        fill["realized_pnl"] = pnl
        self.fills.append(dict(fill))
        return dict(fill)

    def mark(self, reference_price):
        price = number(reference_price, positive=True)
        market_value = (self.quantity * price).quantize(MONEY)
        equity = self.cash + market_value
        unrealized = market_value - self.entry_cost
        if equity - self.starting_cash != self.realized_pnl + unrealized:
            raise ArithmeticError("Simulated ledger does not reconcile.")
        return {
            "cash": self.cash,
            "quantity": self.quantity,
            "market_value": market_value,
            "equity": equity,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": unrealized,
            "total_fees": self.total_fees,
            "valuation": "reference mark; future exit costs not deducted",
        }
