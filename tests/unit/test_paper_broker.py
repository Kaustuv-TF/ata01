"""Paper broker realism: bid/ask, partial fills, rejections, timeouts, bands, holidays, stops."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from ata.brokers.base import (
    BrokerOrderStatus,
    BrokerRejected,
    BrokerTimeout,
    BrokerUnavailable,
    OrderRequest,
    SessionExpired,
)
from ata.brokers.paper import PaperBroker
from ata.data.calendar import IST
from ata.models import Bar, Exchange, OrderType, Product, Segment, Side
from ata.money import D

DAY = date(2026, 11, 3)
T = datetime(2026, 11, 3, 9, 16, tzinfo=IST)
HOLIDAY = datetime(2026, 11, 24, 9, 16, tzinfo=IST)


def bar(o: str, h: str, l: str, c: str, vol: int = 1_000_000, sym: str = "TEST", day: date = DAY) -> Bar:
    return Bar(symbol=sym, day=day, open=D(o), high=D(h), low=D(l), close=D(c), volume=vol)


def req(cid: str = "C1", side: Side = Side.BUY, qty: int = 10, price: str = "100", **kw: object) -> OrderRequest:
    base: dict[str, object] = dict(
        client_order_id=cid,
        symbol="TEST",
        exchange=Exchange.NSE,
        segment=Segment.EQ,
        product=Product.DELIVERY,
        side=side,
        order_type=OrderType.LIMIT,
        qty=qty,
        limit_price=D(price),
    )
    base.update(kw)
    return OrderRequest(**base)  # type: ignore[arg-type]


@pytest.fixture
def broker(paper: PaperBroker) -> PaperBroker:
    paper.open_session(T, {"TEST": bar("100", "100", "100", "100", day=date(2026, 11, 2))})
    return paper


def test_quote_has_spread(broker: PaperBroker) -> None:
    q = broker.quote("TEST", T)
    assert q is not None and q.bid < q.last < q.ask
    assert broker.quote("NOPE", T) is None


def test_buy_limit_fills_with_fees(broker: PaperBroker) -> None:
    broker.place_order(req(), T)
    broker.process_bar(bar("99", "101", "98", "100"), T)
    (o,) = [broker.order_by_client_id("C1")]
    assert o is not None and o.status is BrokerOrderStatus.FILLED and o.avg_fill_price <= D(100)
    t = broker.trades()[0]
    assert t.fees > 0
    assert broker.available_cash() == D(10000) - t.price * 10 - t.fees
    assert broker.positions()["TEST"].qty == 10


def test_unmarketable_limit_does_not_fill(broker: PaperBroker) -> None:
    broker.place_order(req(price="95"), T)
    broker.process_bar(bar("99", "101", "98", "100"), T)
    assert broker.order_by_client_id("C1").status is BrokerOrderStatus.OPEN  # type: ignore[union-attr]


def test_partial_fill_by_participation(broker: PaperBroker) -> None:
    broker.place_order(req(qty=20), T)
    broker.process_bar(bar("99", "101", "98", "100", vol=100), T)  # capacity 5
    o = broker.order_by_client_id("C1")
    assert o is not None and o.status is BrokerOrderStatus.PARTIAL and o.filled_qty == 5


def test_day_orders_expire(broker: PaperBroker) -> None:
    broker.place_order(req(price="95"), T)
    assert broker.end_session() == ["C1"]
    assert broker.order_by_client_id("C1").status is BrokerOrderStatus.CANCELLED  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "kw,reason",
    [
        ({"price": "130"}, "band"),
        ({"price": "100.03"}, "tick"),
        ({"order_type": OrderType.MARKET}, "market orders"),
        ({"segment": Segment.FUT}, "segment/product"),
        ({"product": Product.MARGIN}, "segment/product"),
        ({"qty": 0}, "quantity"),
        ({"qty": 200}, "insufficient funds"),
        ({"symbol": "ASMX"}, "status"),
        ({"symbol": "UNKNOWN"}, "unknown instrument"),
    ],
)
def test_rejections(broker: PaperBroker, kw: dict[str, object], reason: str) -> None:
    with pytest.raises(BrokerRejected, match=reason):
        broker.place_order(req(**kw), T)
    if kw.get("symbol") != "UNKNOWN":
        assert broker.order_by_client_id("C1").status is BrokerOrderStatus.REJECTED  # type: ignore[union-attr]


def test_lot_multiple(broker: PaperBroker) -> None:
    with pytest.raises(BrokerRejected, match="lot"):
        broker.place_order(req(qty=3, symbol="LOT5"), T)


def test_market_holiday_rejected(broker: PaperBroker) -> None:
    with pytest.raises(BrokerRejected, match="holiday"):
        broker.place_order(req(), HOLIDAY)


def test_no_short_selling(broker: PaperBroker) -> None:
    with pytest.raises(BrokerRejected, match="short"):
        broker.place_order(req(side=Side.SELL), T)


def test_sell_cannot_double_count_reserved_qty(broker: PaperBroker) -> None:
    broker.place_order(req(), T)
    broker.process_bar(bar("99", "101", "98", "100"), T)
    broker.place_order(req("S1", Side.SELL, 10, "90", order_type=OrderType.STOP_LIMIT, trigger_price=D("91")), T)
    with pytest.raises(BrokerRejected, match="short"):
        broker.place_order(req("S2", Side.SELL, 10, "105"), T)


def test_duplicate_client_id_rejected(broker: PaperBroker) -> None:
    broker.place_order(req(), T)
    with pytest.raises(BrokerRejected, match="duplicate"):
        broker.place_order(req(), T)


def test_timeout_after_accept_leaves_order_in_book(broker: PaperBroker) -> None:
    broker.faults.timeout_next = "accepted"
    with pytest.raises(BrokerTimeout):
        broker.place_order(req(), T)
    assert broker.order_by_client_id("C1") is not None


def test_timeout_lost_leaves_nothing(broker: PaperBroker) -> None:
    broker.faults.timeout_next = "lost"
    with pytest.raises(BrokerTimeout):
        broker.place_order(req(), T)
    assert broker.order_by_client_id("C1") is None


def test_injected_rejection(broker: PaperBroker) -> None:
    broker.faults.reject_next = "RMS"
    with pytest.raises(BrokerRejected, match="RMS"):
        broker.place_order(req(), T)


def test_unavailable_and_session_expiry(broker: PaperBroker) -> None:
    broker.faults.unavailable = True
    assert not broker.session_valid()
    with pytest.raises(BrokerUnavailable):
        broker.positions()
    broker.faults.unavailable = False
    broker.faults.session_expired = True
    with pytest.raises(SessionExpired):
        broker.place_order(req(), T)


def _hold(broker: PaperBroker) -> None:
    broker.place_order(req(), T)
    broker.process_bar(bar("99", "101", "98", "100"), T)
    broker.place_order(req("S1", Side.SELL, 10, "94", order_type=OrderType.STOP_LIMIT, trigger_price=D("95")), T)


def test_stop_limit_triggers_and_fills(broker: PaperBroker) -> None:
    _hold(broker)
    broker.process_bar(bar("99", "99.5", "93", "94", day=date(2026, 11, 4)), T)
    o = broker.order_by_client_id("S1")
    assert o is not None and o.status is BrokerOrderStatus.FILLED and D(94) <= o.avg_fill_price <= D(95)


def test_stop_limit_gap_below_limit_stays_unfilled(broker: PaperBroker) -> None:
    _hold(broker)
    broker.process_bar(bar("90", "91", "89", "90", day=date(2026, 11, 4)), T)
    o = broker.order_by_client_id("S1")
    assert o is not None and o.status is BrokerOrderStatus.OPEN and o.filled_qty == 0 and "gap" in o.reason
    assert broker.positions()["TEST"].qty == 10  # exposure remains: the stop is not a guarantee


def test_stop_persists_over_session_end(broker: PaperBroker) -> None:
    _hold(broker)
    broker.end_session()
    assert broker.order_by_client_id("S1").status is BrokerOrderStatus.TRIGGER_PENDING  # type: ignore[union-attr]


def test_cancel(broker: PaperBroker) -> None:
    bid = broker.place_order(req(price="95"), T)
    broker.cancel_order(bid, T)
    with pytest.raises(BrokerRejected):
        broker.cancel_order(bid, T)
