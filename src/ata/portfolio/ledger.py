"""Internal portfolio ledger, built only from broker-confirmed fills.
Broker truth supersedes it: reconciliation compares the two and halts on drift."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ata.brokers.base import BrokerTrade
from ata.models import Side
from ata.money import ZERO, inr


@dataclass
class Position:
    symbol: str
    qty: int
    avg_cost: Decimal
    stop_price: Decimal
    target_price: Decimal
    intent_key: str
    strategy_id: str
    opened: date
    horizon_days: int
    fees_paid: Decimal = ZERO

    @property
    def stop_risk(self) -> Decimal:
        """Estimated loss vs cost basis if the stop fills at its price (gaps can exceed this)."""
        return max(ZERO, self.avg_cost - self.stop_price) * self.qty


@dataclass
class ClosedTrade:
    symbol: str
    strategy_id: str
    intent_key: str
    qty: int
    entry: Decimal
    exit: Decimal
    fees: Decimal
    pnl_after_fees: Decimal
    opened: date
    closed: date
    reason: str


@dataclass
class Ledger:
    cash: Decimal
    positions: dict[str, Position] = field(default_factory=dict)
    closed: list[ClosedTrade] = field(default_factory=list)
    fees_total: Decimal = ZERO
    realized_pnl: Decimal = ZERO

    def apply_entry_fill(self, t: BrokerTrade, *, stop: Decimal, target: Decimal, intent_key: str, strategy_id: str, horizon: int) -> None:
        assert t.side is Side.BUY
        self.cash = inr(self.cash - t.price * t.qty - t.fees)
        self.fees_total += t.fees
        pos = self.positions.get(t.symbol)
        if pos is None:
            self.positions[t.symbol] = Position(
                t.symbol, t.qty, t.price, stop, target, intent_key, strategy_id, t.ts.date(), horizon, t.fees
            )
        else:
            pos.avg_cost = (pos.avg_cost * pos.qty + t.price * t.qty) / (pos.qty + t.qty)
            pos.qty += t.qty
            pos.fees_paid += t.fees

    def apply_exit_fill(self, t: BrokerTrade, reason: str) -> None:
        assert t.side is Side.SELL
        pos = self.positions[t.symbol]
        self.cash = inr(self.cash + t.price * t.qty - t.fees)
        self.fees_total += t.fees
        entry_fees_share = pos.fees_paid * t.qty / pos.qty
        pnl = inr((t.price - pos.avg_cost) * t.qty - t.fees - entry_fees_share)
        self.realized_pnl += pnl
        self.closed.append(
            ClosedTrade(
                t.symbol,
                pos.strategy_id,
                pos.intent_key,
                t.qty,
                pos.avg_cost,
                t.price,
                inr(t.fees + entry_fees_share),
                pnl,
                pos.opened,
                t.ts.date(),
                reason,
            )
        )
        pos.fees_paid -= entry_fees_share
        pos.qty -= t.qty
        if pos.qty == 0:
            del self.positions[t.symbol]

    def gross_invested(self, marks: dict[str, Decimal]) -> Decimal:
        return sum((p.qty * marks.get(p.symbol, p.avg_cost) for p in self.positions.values()), ZERO)

    def cost_basis(self, symbol: str) -> Decimal:
        p = self.positions.get(symbol)
        return p.qty * p.avg_cost if p else ZERO

    def open_risk(self) -> Decimal:
        return sum((p.stop_risk for p in self.positions.values()), ZERO)

    def equity(self, marks: dict[str, Decimal]) -> Decimal:
        return inr(self.cash + self.gross_invested(marks))
