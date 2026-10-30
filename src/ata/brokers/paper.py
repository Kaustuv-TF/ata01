"""Paper broker: an exchange/broker simulator for offline runs.

Models bid/ask spread, limit and stop-limit fills on daily bars, partial fills
(volume participation), rejections, price bands, market holidays, timeouts
(order accepted or lost), connectivity loss and session expiry. Fees use the
configured CostModel estimates. This is a simulation, not a fill guarantee.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ata.backtest.costs import CostModel
from ata.brokers.base import (
    BrokerOrder,
    BrokerOrderStatus,
    BrokerPosition,
    BrokerRejected,
    BrokerTimeout,
    BrokerTrade,
    BrokerUnavailable,
    OrderRequest,
    SessionExpired,
)
from ata.data.calendar import MarketCalendar
from ata.data.instruments import InstrumentMaster
from ata.models import Bar, InstrumentStatus, OrderType, Product, Quote, Segment, Side
from ata.money import ZERO, D, ceil_to_tick, floor_to_tick, inr

ACTIVE = {BrokerOrderStatus.OPEN, BrokerOrderStatus.TRIGGER_PENDING, BrokerOrderStatus.PARTIAL}


@dataclass
class FaultPlan:
    """One-shot fault injection for chaos tests and demo scenarios."""

    reject_next: str | None = None
    timeout_next: str | None = None  # "accepted" (order lands) or "lost" (never reaches book)
    partial_next: int | None = None  # cap the next fill at this qty
    unavailable: bool = False
    session_expired: bool = False


@dataclass
class _Pos:
    qty: int = 0
    avg: Decimal = ZERO


@dataclass
class PaperBroker:
    instruments: InstrumentMaster
    calendar: MarketCalendar
    costs: CostModel
    cash: Decimal
    spread_bps: Decimal = D(10)
    fill_participation: Decimal = D("0.05")
    faults: FaultPlan = field(default_factory=FaultPlan)
    name: str = "paper"

    def __post_init__(self) -> None:
        self._orders: dict[str, BrokerOrder] = {}
        self._by_client: dict[str, str] = {}
        self._positions: dict[str, _Pos] = {}
        self._trades: list[BrokerTrade] = []
        self._last_bar: dict[str, Bar] = {}
        self._prev_close: dict[str, Decimal] = {}
        self._day: datetime | None = None
        self._ids = itertools.count(1)

    # ---- market state ----
    def open_session(self, ts: datetime, prev_bars: dict[str, Bar]) -> None:
        self._day = ts
        for sym, bar in prev_bars.items():
            self._prev_close[sym] = bar.close
            self._last_bar[sym] = bar

    def quote(self, symbol: str, ts: datetime) -> Quote | None:
        bar = self._last_bar.get(symbol)
        if bar is None:
            return None
        half = bar.close * self.spread_bps / D(20000)
        return Quote(
            symbol=symbol,
            ts=ts,
            bid=bar.close - half,
            ask=bar.close + half,
            last=bar.close,
            prev_close=self._prev_close.get(symbol, bar.close),
        )

    # ---- guards ----
    def _guard(self) -> None:
        if self.faults.unavailable:
            raise BrokerUnavailable("paper: broker unreachable")
        if self.faults.session_expired:
            raise SessionExpired("paper: session expired; human re-login required")

    def session_valid(self) -> bool:
        return not self.faults.session_expired and not self.faults.unavailable

    # ---- reads ----
    def available_cash(self) -> Decimal:
        self._guard()
        return self.cash

    def positions(self) -> dict[str, BrokerPosition]:
        self._guard()
        return {s: BrokerPosition(symbol=s, qty=p.qty, avg_price=p.avg) for s, p in self._positions.items() if p.qty}

    def open_orders(self) -> list[BrokerOrder]:
        self._guard()
        return [o.model_copy() for o in self._orders.values() if o.status in ACTIVE]

    def order_by_client_id(self, client_order_id: str) -> BrokerOrder | None:
        self._guard()
        bid = self._by_client.get(client_order_id)
        return self._orders[bid].model_copy() if bid else None

    def trades(self) -> list[BrokerTrade]:
        self._guard()
        return list(self._trades)

    # ---- writes ----
    def _validate(self, req: OrderRequest, now: datetime) -> str | None:
        inst = self.instruments.get(req.symbol)
        if inst is None:
            return "unknown instrument"
        if not self.calendar.is_trading_day(now.date()):
            return "market holiday"
        if inst.status is not InstrumentStatus.ACTIVE:
            return f"instrument status {inst.status}"
        if req.segment in (Segment.FUT, Segment.OPT) or req.product is not Product.DELIVERY:
            return "segment/product not enabled"
        if req.order_type is OrderType.MARKET:
            return "market orders not permitted"
        if req.qty <= 0 or req.qty % inst.lot:
            return "quantity not a positive multiple of lot"
        if req.limit_price % inst.tick or (req.trigger_price is not None and req.trigger_price % inst.tick):
            return "price not a tick multiple"
        prev = self._prev_close.get(req.symbol)
        if prev is not None:
            lo, hi = inst.band(prev)
            if not lo <= req.limit_price <= hi:
                return "price outside daily band"
        if req.side is Side.BUY:
            need = req.limit_price * req.qty + self.costs.leg(Side.BUY, req.qty, req.limit_price).total
            pending = sum(
                (o.limit_price * (o.qty - o.filled_qty) for o in self._orders.values() if o.status in ACTIVE and o.side is Side.BUY), ZERO
            )
            if need + pending > self.cash:
                return "insufficient funds"
        else:
            held = self._positions.get(req.symbol, _Pos()).qty
            reserved = sum(
                o.qty - o.filled_qty for o in self._orders.values() if o.status in ACTIVE and o.side is Side.SELL and o.symbol == req.symbol
            )
            if req.qty + reserved > held:
                return "sell exceeds free holdings (no short selling)"
        return None

    def place_order(self, req: OrderRequest, now: datetime) -> str:
        self._guard()
        if req.client_order_id in self._by_client:
            raise BrokerRejected("duplicate client order id")
        if self.faults.timeout_next == "lost":
            self.faults.timeout_next = None
            raise BrokerTimeout("paper: request lost before reaching broker")
        if reason := self.faults.reject_next:
            self.faults.reject_next = None
            raise BrokerRejected(reason)
        reason = self._validate(req, now)
        bid = f"P{next(self._ids):06d}"
        status = (
            BrokerOrderStatus.REJECTED
            if reason
            else (BrokerOrderStatus.TRIGGER_PENDING if req.order_type is OrderType.STOP_LIMIT else BrokerOrderStatus.OPEN)
        )
        self._orders[bid] = BrokerOrder(
            client_order_id=req.client_order_id,
            broker_order_id=bid,
            symbol=req.symbol,
            side=req.side,
            order_type=req.order_type,
            qty=req.qty,
            status=status,
            reason=reason or "",
            limit_price=req.limit_price,
            trigger_price=req.trigger_price,
        )
        self._by_client[req.client_order_id] = bid
        if self.faults.timeout_next == "accepted":
            self.faults.timeout_next = None
            raise BrokerTimeout("paper: response lost after order accepted")
        if reason:
            raise BrokerRejected(reason)
        return bid

    def cancel_order(self, broker_order_id: str, now: datetime) -> None:
        self._guard()
        order = self._orders.get(broker_order_id)
        if order is None or order.status not in ACTIVE:
            raise BrokerRejected("order not cancellable")
        order.status = BrokerOrderStatus.CANCELLED
        order.reason = "cancelled by client"

    # ---- simulation ----
    def process_bar(self, bar: Bar, ts: datetime) -> None:
        """Match active orders for bar.symbol against the day's OHLC, then mark the bar."""
        half_spread = bar.open * self.spread_bps / D(20000)
        capacity = max(1, int(D(bar.volume) * self.fill_participation))
        for order in [o for o in self._orders.values() if o.symbol == bar.symbol and o.status in ACTIVE]:
            price: Decimal | None = None
            if order.order_type is OrderType.LIMIT and order.side is Side.BUY and bar.low <= order.limit_price:
                price = min(order.limit_price, bar.open + half_spread)
            elif order.order_type is OrderType.LIMIT and order.side is Side.SELL and bar.high >= order.limit_price:
                price = max(order.limit_price, bar.open - half_spread)
            elif order.order_type is OrderType.STOP_LIMIT and order.trigger_price is not None:
                if order.status is BrokerOrderStatus.TRIGGER_PENDING and bar.low <= order.trigger_price:
                    order.status = BrokerOrderStatus.OPEN
                    if bar.open < order.trigger_price:  # gap through the stop
                        price = bar.open - half_spread if bar.open - half_spread >= order.limit_price else None
                        if price is None:
                            order.reason = "gap below stop-limit; unfilled"
                    else:
                        price = max(order.limit_price, order.trigger_price - half_spread)
                elif order.status is not BrokerOrderStatus.TRIGGER_PENDING and bar.high >= order.limit_price:
                    price = max(order.limit_price, bar.open - half_spread)
            if price is None:
                continue
            qty = min(order.qty - order.filled_qty, capacity)
            if self.faults.partial_next is not None:
                qty = min(qty, self.faults.partial_next)
                self.faults.partial_next = None
            if qty > 0:
                inst = self.instruments.get(order.symbol)
                assert inst is not None
                # trades print on the tick grid; round against us
                if order.side is Side.BUY:
                    price = min(ceil_to_tick(price, inst.tick), order.limit_price)
                else:
                    price = max(floor_to_tick(price, inst.tick), order.limit_price)
                self._fill(order, qty, price, ts)
        self._last_bar[bar.symbol] = bar

    def _fill(self, order: BrokerOrder, qty: int, price: Decimal, ts: datetime) -> None:
        fees = self.costs.leg(order.side, qty, price).total
        pos = self._positions.setdefault(order.symbol, _Pos())
        if order.side is Side.BUY:
            pos.avg = (pos.avg * pos.qty + price * qty) / (pos.qty + qty)
            pos.qty += qty
            self.cash -= price * qty + fees
        else:
            pos.qty -= qty
            self.cash += price * qty - fees
            if pos.qty == 0:
                pos.avg = ZERO
        order.avg_fill_price = (order.avg_fill_price * order.filled_qty + price * qty) / (order.filled_qty + qty)
        order.filled_qty += qty
        order.status = BrokerOrderStatus.FILLED if order.filled_qty == order.qty else BrokerOrderStatus.PARTIAL
        self.cash = inr(self.cash)
        self._trades.append(
            BrokerTrade(
                trade_id=f"T{len(self._trades) + 1:06d}",
                client_order_id=order.client_order_id,
                broker_order_id=order.broker_order_id,
                symbol=order.symbol,
                side=order.side,
                qty=qty,
                price=price,
                fees=fees,
                ts=ts,
            )
        )

    def end_session(self) -> list[str]:
        """Expire DAY limit orders. Stop-limits persist (GTT-like; broker support UNVERIFIED)."""
        expired = []
        for o in self._orders.values():
            if o.status in ACTIVE and o.order_type is OrderType.LIMIT:
                o.status = BrokerOrderStatus.CANCELLED
                o.reason = "day order expired"
                expired.append(o.client_order_id)
        return expired
