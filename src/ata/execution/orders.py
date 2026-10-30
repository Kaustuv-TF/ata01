r"""Order management: explicit state machine, idempotent client IDs, no blind retries.

CREATED -> RISK_APPROVED -> SUBMITTED -> ACKNOWLEDGED -> PART_FILLED/FILLED
                                     \-> REJECTED / CANCELLED / UNKNOWN
A timeout or connectivity error moves an order to UNKNOWN and halts new entries
until the broker order book has been queried. UNKNOWN is never resubmitted.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from ata.brokers.base import (
    Broker,
    BrokerError,
    BrokerOrderStatus,
    BrokerRejected,
    BrokerTrade,
    OrderRequest,
)
from ata.money import ZERO
from ata.ops.store import Store


class OrderState(StrEnum):
    CREATED = "CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PART_FILLED = "PART_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class Purpose(StrEnum):
    ENTRY = "ENTRY"
    STOP = "STOP"
    EXIT = "EXIT"


S = OrderState
TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    S.CREATED: frozenset({S.RISK_APPROVED, S.REJECTED}),
    S.RISK_APPROVED: frozenset({S.SUBMITTED, S.CANCELLED}),
    S.SUBMITTED: frozenset({S.ACKNOWLEDGED, S.REJECTED, S.UNKNOWN}),
    S.ACKNOWLEDGED: frozenset({S.PART_FILLED, S.FILLED, S.CANCELLED, S.REJECTED, S.UNKNOWN}),
    S.PART_FILLED: frozenset({S.PART_FILLED, S.FILLED, S.CANCELLED, S.UNKNOWN}),
    S.UNKNOWN: frozenset({S.ACKNOWLEDGED, S.PART_FILLED, S.FILLED, S.REJECTED, S.CANCELLED}),
    S.FILLED: frozenset(),
    S.REJECTED: frozenset(),
    S.CANCELLED: frozenset(),
}
TERMINAL = frozenset({S.FILLED, S.REJECTED, S.CANCELLED})
UNKNOWN_CONFIRMATIONS = 2  # consecutive "not at broker" checks before an UNKNOWN is closed


class IllegalTransition(RuntimeError):
    pass


class DuplicateOrder(RuntimeError):
    pass


class NewEntriesHalted(RuntimeError):
    pass


class RateLimited(RuntimeError):
    pass


def client_order_id(intent_key: str, purpose: Purpose, seq: int = 0) -> str:
    """Deterministic, so a replayed intent maps to the same ID and is refused as a duplicate."""
    return "ATA" + hashlib.sha256(f"{intent_key}|{purpose}|{seq}".encode()).hexdigest()[:17].upper()


@dataclass
class ManagedOrder:
    request: OrderRequest
    intent_key: str
    purpose: Purpose
    state: OrderState = OrderState.CREATED
    broker_order_id: str | None = None
    filled_qty: int = 0
    avg_price: Decimal = ZERO
    not_found_checks: int = 0
    history: list[tuple[str, str, str, str]] = field(default_factory=list)

    @property
    def cid(self) -> str:
        return self.request.client_order_id

    @property
    def active(self) -> bool:
        return self.state not in TERMINAL


@dataclass
class Fill:
    trade: BrokerTrade
    purpose: Purpose
    intent_key: str


class OrderManager:
    def __init__(
        self, broker: Broker, store: Store, max_orders_per_second: int = 1, throttle: Callable[[timedelta], None] | None = None
    ) -> None:
        """throttle(delay) is called to wait out the rate limit (time.sleep live, clock advance in sim).
        Without one, a too-fast submit raises instead of waiting."""
        self.broker = broker
        self.throttle = throttle
        self.store = store
        self.min_interval = timedelta(seconds=1) / max_orders_per_second
        self.orders: dict[str, ManagedOrder] = {}
        self._seen_trades: set[str] = set()
        self._last_submit: datetime | None = None
        self.entry_halt_reasons: set[str] = set()

    # ---- state machine ----
    def transition(self, order: ManagedOrder, to: OrderState, now: datetime, note: str = "") -> None:
        if to not in TRANSITIONS[order.state]:
            raise IllegalTransition(f"{order.cid}: {order.state} -> {to}")
        order.history.append((now.isoformat(), order.state.value, to.value, note))
        self.store.audit(
            "oms",
            "order_state",
            {
                "cid": order.cid,
                "intent": order.intent_key,
                "purpose": order.purpose,
                "symbol": order.request.symbol,
                "side": order.request.side,
                "qty": order.request.qty,
                "from": order.state,
                "to": to,
                "note": note,
            },
            now,
        )
        order.state = to

    # ---- lifecycle ----
    def create(self, request: OrderRequest, intent_key: str, purpose: Purpose, now: datetime) -> ManagedOrder:
        if request.client_order_id in self.orders:
            raise DuplicateOrder(request.client_order_id)
        order = ManagedOrder(request=request, intent_key=intent_key, purpose=purpose)
        self.orders[order.cid] = order
        self.store.audit("oms", "order_created", {"cid": order.cid, "purpose": purpose, "request": request.model_dump(mode="json")}, now)
        return order

    def next_submit_time(self, now: datetime) -> datetime:
        if self._last_submit is None:
            return now
        return max(now, self._last_submit + self.min_interval)

    def submit(self, order: ManagedOrder, now: datetime) -> None:
        if order.state is not OrderState.RISK_APPROVED:
            raise IllegalTransition(f"{order.cid}: submit requires RISK_APPROVED, is {order.state}")
        if order.purpose is Purpose.ENTRY and self.entry_halt_reasons:
            raise NewEntriesHalted(", ".join(sorted(self.entry_halt_reasons)))
        earliest = self.next_submit_time(now)
        if earliest > now:
            if self.throttle is None:
                raise RateLimited(f"next order allowed at {earliest.isoformat()}")
            self.throttle(earliest - now)
            now = earliest
        self._last_submit = now
        self.transition(order, OrderState.SUBMITTED, now)
        try:
            order.broker_order_id = self.broker.place_order(order.request, now)
        except BrokerRejected as exc:
            self.transition(order, OrderState.REJECTED, now, str(exc))
            return
        except BrokerError as exc:  # timeout, unavailable, session: outcome unknown
            self.transition(order, OrderState.UNKNOWN, now, f"{type(exc).__name__}: {exc}")
            self.entry_halt_reasons.add("unknown order status")
            return
        self.transition(order, OrderState.ACKNOWLEDGED, now)

    def cancel(self, order: ManagedOrder, now: datetime) -> None:
        if order.state is OrderState.RISK_APPROVED:
            self.transition(order, OrderState.CANCELLED, now, "never submitted")
            return
        if not order.active or order.broker_order_id is None:
            return
        try:
            self.broker.cancel_order(order.broker_order_id, now)
        except BrokerRejected:
            pass  # already filled/cancelled; sync() will learn the truth
        except BrokerError as exc:
            self.entry_halt_reasons.add("cancel outcome unknown")
            self.store.audit("oms", "cancel_unknown", {"cid": order.cid, "error": str(exc)}, now)
            return
        self.sync(now)

    def sync(self, now: datetime) -> list[Fill]:
        """Pull broker truth for every active order; return newly seen fills."""
        fills: list[Fill] = []
        trades = self.broker.trades()
        for order in [o for o in self.orders.values() if o.active and o.state is not OrderState.RISK_APPROVED]:
            remote = self.broker.order_by_client_id(order.cid)
            if remote is None:
                if order.state is OrderState.UNKNOWN:
                    order.not_found_checks += 1
                    if order.not_found_checks >= UNKNOWN_CONFIRMATIONS:
                        self.transition(order, OrderState.REJECTED, now, "not found at broker after timeout (confirmed)")
                continue
            order.broker_order_id = remote.broker_order_id
            for t in trades:
                if t.client_order_id == order.cid and t.trade_id not in self._seen_trades:
                    self._seen_trades.add(t.trade_id)
                    fills.append(Fill(trade=t, purpose=order.purpose, intent_key=order.intent_key))
                    self.store.audit("oms", "fill", {**t.model_dump(mode="json"), "purpose": order.purpose}, now)
            prev_filled = order.filled_qty
            order.filled_qty, order.avg_price = remote.filled_qty, remote.avg_fill_price
            target = {
                BrokerOrderStatus.OPEN: OrderState.ACKNOWLEDGED,
                BrokerOrderStatus.TRIGGER_PENDING: OrderState.ACKNOWLEDGED,
                BrokerOrderStatus.PARTIAL: OrderState.PART_FILLED,
                BrokerOrderStatus.FILLED: OrderState.FILLED,
                BrokerOrderStatus.CANCELLED: OrderState.CANCELLED,
                BrokerOrderStatus.REJECTED: OrderState.REJECTED,
            }[remote.status]
            if target is OrderState.ACKNOWLEDGED and order.state is OrderState.PART_FILLED:
                continue
            changed = target is not order.state or (target is OrderState.PART_FILLED and remote.filled_qty != prev_filled)
            if changed:
                note = f"filled {remote.filled_qty}/{order.request.qty}" if remote.filled_qty else remote.reason
                self.transition(order, target, now, note)
        if not any(o.state is OrderState.UNKNOWN for o in self.orders.values()):
            self.entry_halt_reasons.discard("unknown order status")
        return fills

    def active_orders(self, purpose: Purpose | None = None, symbol: str | None = None) -> list[ManagedOrder]:
        return [
            o
            for o in self.orders.values()
            if o.active and (purpose is None or o.purpose is purpose) and (symbol is None or o.request.symbol == symbol)
        ]
