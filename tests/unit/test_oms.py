"""Order state machine, idempotency, timeout handling (never blind-resubmit), rate limit."""

from __future__ import annotations

import itertools
from datetime import date, datetime, timedelta

import pytest

from ata.brokers.paper import PaperBroker
from ata.data.calendar import IST
from ata.execution.orders import (
    TERMINAL,
    TRANSITIONS,
    DuplicateOrder,
    IllegalTransition,
    ManagedOrder,
    NewEntriesHalted,
    OrderManager,
    OrderState,
    Purpose,
    RateLimited,
    client_order_id,
)
from ata.ops.store import Store
from tests.unit.test_paper_broker import bar, req

T = datetime(2026, 11, 3, 9, 16, tzinfo=IST)


@pytest.fixture
def broker(paper: PaperBroker) -> PaperBroker:
    paper.open_session(T, {"TEST": bar("100", "100", "100", "100", day=date(2026, 11, 2))})
    return paper


@pytest.fixture
def oms(broker: PaperBroker, store: Store) -> OrderManager:
    return OrderManager(broker, store, 1, throttle=lambda _: None)


def approved(oms: OrderManager, cid: str = "C1", purpose: Purpose = Purpose.ENTRY, **kw: object) -> ManagedOrder:
    o = oms.create(req(cid, **kw), "intent-1", purpose, T)
    oms.transition(o, OrderState.RISK_APPROVED, T)
    return o


def test_client_order_id_is_deterministic() -> None:
    assert client_order_id("k", Purpose.ENTRY) == client_order_id("k", Purpose.ENTRY)
    assert client_order_id("k", Purpose.ENTRY) != client_order_id("k", Purpose.STOP)
    assert len(client_order_id("k", Purpose.ENTRY)) == 20


def test_happy_path_states(oms: OrderManager, broker: PaperBroker) -> None:
    o = approved(oms)
    oms.submit(o, T)
    assert o.state is OrderState.ACKNOWLEDGED
    broker.process_bar(bar("99", "101", "98", "100"), T)
    fills = oms.sync(T)
    assert o.state is OrderState.FILLED and len(fills) == 1 and fills[0].purpose is Purpose.ENTRY
    assert [h[2] for h in o.history] == ["RISK_APPROVED", "SUBMITTED", "ACKNOWLEDGED", "FILLED"]
    assert oms.sync(T) == []  # fills are reported once


def test_every_illegal_transition_raises(oms: OrderManager) -> None:
    for src, dst in itertools.product(OrderState, OrderState):
        o = ManagedOrder(request=req(f"X{src}{dst}"), intent_key="i", purpose=Purpose.ENTRY, state=src)
        if dst in TRANSITIONS[src]:
            oms.transition(o, dst, T)
        else:
            with pytest.raises(IllegalTransition):
                oms.transition(o, dst, T)


def test_terminal_states_are_final() -> None:
    for s in TERMINAL:
        assert TRANSITIONS[s] == frozenset()
    assert OrderState.SUBMITTED not in TRANSITIONS[OrderState.UNKNOWN]  # no resubmission path


def test_submit_requires_risk_approval(oms: OrderManager) -> None:
    o = oms.create(req(), "intent-1", Purpose.ENTRY, T)
    with pytest.raises(IllegalTransition):
        oms.submit(o, T)


def test_duplicate_intent_is_refused(oms: OrderManager) -> None:
    approved(oms)
    with pytest.raises(DuplicateOrder):
        oms.create(req("C1"), "intent-1", Purpose.ENTRY, T)


def test_timeout_accepted_goes_unknown_then_reconciles(oms: OrderManager, broker: PaperBroker) -> None:
    broker.faults.timeout_next = "accepted"
    o = approved(oms)
    oms.submit(o, T)
    assert o.state is OrderState.UNKNOWN and oms.entry_halt_reasons
    oms.sync(T)
    assert o.state is OrderState.ACKNOWLEDGED and not oms.entry_halt_reasons
    assert len([x for x in broker._orders.values() if x.client_order_id == "C1"]) == 1  # never resubmitted


def test_timeout_lost_is_closed_only_after_confirmations(oms: OrderManager, broker: PaperBroker) -> None:
    broker.faults.timeout_next = "lost"
    o = approved(oms)
    oms.submit(o, T)
    oms.sync(T)
    assert o.state is OrderState.UNKNOWN and oms.entry_halt_reasons
    oms.sync(T + timedelta(seconds=30))
    assert o.state is OrderState.REJECTED and not oms.entry_halt_reasons
    assert broker.order_by_client_id("C1") is None  # still not placed: no blind retry


def test_entries_halted_while_unknown_but_stops_allowed(oms: OrderManager, broker: PaperBroker) -> None:
    broker.faults.timeout_next = "lost"
    oms.submit(approved(oms), T)
    with pytest.raises(NewEntriesHalted):
        oms.submit(approved(oms, "C2"), T + timedelta(seconds=2))
    broker.place_order(req("BUYHELD"), T)
    broker.process_bar(bar("99", "101", "98", "100"), T)
    from ata.models import OrderType, Side
    from ata.money import D

    stop = approved(oms, "S1", Purpose.STOP, side=Side.SELL, order_type=OrderType.STOP_LIMIT, price="94", trigger_price=D("95"))
    oms.submit(stop, T + timedelta(seconds=3))
    assert stop.state is OrderState.ACKNOWLEDGED


def test_broker_rejection(oms: OrderManager, broker: PaperBroker) -> None:
    o = approved(oms, price="150")
    oms.submit(o, T)
    assert o.state is OrderState.REJECTED and "band" in o.history[-1][3]


def test_partial_fill_states(oms: OrderManager, broker: PaperBroker) -> None:
    o = approved(oms, qty=20)
    oms.submit(o, T)
    broker.process_bar(bar("99", "101", "98", "100", vol=100), T)
    oms.sync(T)
    assert o.state is OrderState.PART_FILLED and o.filled_qty == 5
    broker.process_bar(bar("99", "101", "98", "100", vol=100), T)
    oms.sync(T)
    assert o.state is OrderState.PART_FILLED and o.filled_qty == 10
    broker.end_session()
    oms.sync(T)
    assert o.state is OrderState.CANCELLED and o.filled_qty == 10


def test_rate_limit_without_throttle_raises(broker: PaperBroker, store: Store) -> None:
    oms = OrderManager(broker, store, 1)
    oms.submit(approved(oms, "C1", price="95"), T)
    with pytest.raises(RateLimited):
        oms.submit(approved(oms, "C2", price="95"), T + timedelta(milliseconds=500))
    oms.submit(approved(oms, "C3", price="95"), T + timedelta(seconds=1))


def test_rate_limit_throttle_waits(broker: PaperBroker, store: Store) -> None:
    waits: list[timedelta] = []
    oms = OrderManager(broker, store, 1, throttle=waits.append)
    oms.submit(approved(oms, "C1", price="95"), T)
    oms.submit(approved(oms, "C2", price="95"), T)
    assert waits == [timedelta(seconds=1)]


def test_cancel_unsubmitted_and_submitted(oms: OrderManager) -> None:
    a = approved(oms, "C1", price="95")
    oms.cancel(a, T)
    assert a.state is OrderState.CANCELLED
    b = approved(oms, "C2", price="95")
    oms.submit(b, T)
    oms.cancel(b, T + timedelta(seconds=1))
    assert b.state is OrderState.CANCELLED


def test_cancel_with_broker_down_halts_entries(oms: OrderManager, broker: PaperBroker) -> None:
    b = approved(oms, "C2", price="95")
    oms.submit(b, T)
    broker.faults.unavailable = True
    oms.cancel(b, T)
    assert b.state is OrderState.ACKNOWLEDGED  # unknown outcome: not assumed cancelled
    assert "cancel outcome unknown" in oms.entry_halt_reasons


def test_every_transition_is_audited(oms: OrderManager, store: Store) -> None:
    oms.submit(approved(oms), T)
    assert [r["payload"]["to"] for r in store.audit_rows("order_state")] == ["RISK_APPROVED", "SUBMITTED", "ACKNOWLEDGED"]
