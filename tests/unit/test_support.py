"""Costs, data validation, calendar, ledger, reconciliation, stops, strategies, settings, Paytm stub."""

from __future__ import annotations

import inspect
import re
from datetime import date, datetime, timedelta

import pytest

from ata.backtest.costs import CostModel
from ata.brokers import paytmmoney
from ata.brokers.base import BrokerTrade, LiveTradingDisabled
from ata.brokers.paper import PaperBroker
from ata.data.calendar import IST, MarketCalendar
from ata.data.history import synthetic_bars
from ata.data.instruments import InstrumentMaster
from ata.data.validators import validate_bar, validate_history, validate_quote
from ata.execution.orders import OrderManager, OrderState, Purpose
from ata.execution.reconciliation import reconcile
from ata.execution.stops import stop_limit_price, sync_protection
from ata.models import Bar, Side
from ata.money import D
from ata.ops.store import Store
from ata.portfolio.ledger import Ledger
from ata.settings import Settings
from ata.strategies.catalog import APPROVED
from tests.conftest import CONFIG, make_instrument, make_quote
from tests.unit.test_paper_broker import bar, req

T = datetime(2026, 11, 3, 9, 16, tzinfo=IST)


# ---- costs ----
def test_costs_are_flagged_unverified(costs: CostModel) -> None:
    assert costs.verified is False and "placeholder" in costs.source


def test_cost_legs(costs: CostModel) -> None:
    buy, sell = costs.leg(Side.BUY, 10, D(250)), costs.leg(Side.SELL, 10, D(250))
    assert buy.brokerage == D(20) and buy.stamp > 0 and buy.dp == 0
    assert sell.dp == D("15.93") and sell.stamp == 0
    assert costs.leg(Side.BUY, 1, D(100)).brokerage == D("2.5")  # pct cap below flat fee
    assert costs.leg(Side.BUY, 0, D(100)).total == 0
    assert costs.round_trip(10, D(250), D(260)) > buy.total + sell.total


def test_round_trip_costs_dominate_micro_trades(costs: CostModel) -> None:
    """A full INR 2,500 position costs more round trip than the INR 50 risk budget."""
    assert costs.round_trip(25, D(100), D(100)) > D(50)


# ---- data ----
def test_bar_validation() -> None:
    good = Bar(symbol="X", day=date(2026, 11, 3), open=D(10), high=D(11), low=D(9), close=D(10), volume=1)
    assert validate_bar(good).ok
    assert not validate_bar(good.model_copy(update={"low": D(0)})).ok
    assert not validate_bar(good.model_copy(update={"high": D("9.5")})).ok
    assert not validate_bar(good.model_copy(update={"volume": -1})).ok
    assert not validate_bar(good, prev=good).ok
    assert not validate_history([good, good]).ok
    assert validate_history([good]).ok


def test_quote_validation() -> None:
    age, spread = timedelta(minutes=15), D(60)
    assert validate_quote(make_quote(ts=T), T, age, spread).ok
    assert "stale" in validate_quote(make_quote(ts=T - timedelta(minutes=16)), T, age, spread).reason
    assert "future" in validate_quote(make_quote(ts=T + timedelta(minutes=1)), T, age, spread).reason
    assert "spread" in validate_quote(make_quote(spread_bps=100, ts=T), T, age, spread).reason
    crossed = make_quote(ts=T).model_copy(update={"bid": D(101), "ask": D(100)})
    assert "crossed" in validate_quote(crossed, T, age, spread).reason
    zero = make_quote(ts=T).model_copy(update={"bid": D(0)})
    assert not validate_quote(zero, T, age, spread).ok


def test_calendar(calendar: MarketCalendar) -> None:
    assert calendar.is_trading_day(date(2026, 11, 3))
    assert not calendar.is_trading_day(date(2026, 11, 7))  # Saturday
    assert not calendar.is_trading_day(date(2026, 11, 24))  # configured holiday
    assert calendar.next_trading_day(date(2026, 11, 23)) == date(2026, 11, 25)
    assert calendar.is_open(datetime(2026, 11, 3, 9, 15, tzinfo=IST))
    assert not calendar.is_open(datetime(2026, 11, 3, 15, 30, tzinfo=IST))
    assert not calendar.is_open(datetime(2026, 11, 3, 3, 0, tzinfo=IST))
    assert calendar.verified is False
    assert len(calendar.trading_days(date(2026, 11, 7), 3)) == 3


def test_synthetic_bars_are_valid_and_deterministic(calendar: MarketCalendar) -> None:
    inst = InstrumentMaster.load(CONFIG / "instruments.yaml").all()[0]
    days = calendar.trading_days(date(2026, 1, 1), 200)
    a, b = synthetic_bars(inst, days, 1), synthetic_bars(inst, days, 1)
    assert a == b and validate_history(a).ok
    assert all(x.close % inst.tick == 0 for x in a)


# ---- strategies ----
def test_strategies_are_long_only_and_versioned(calendar: MarketCalendar) -> None:
    master = InstrumentMaster.load(CONFIG / "instruments.yaml")
    days = calendar.trading_days(date(2026, 1, 1), 250)
    seen = 0
    for inst in master.all():
        bars = synthetic_bars(inst, days, 3)
        for n in range(100, 250):
            for s in APPROVED.values():
                sig = s.evaluate(inst, bars[:n])
                if sig:
                    seen += 1
                    assert sig.side is Side.BUY and sig.stop_price < sig.reference_price < sig.target_price
                    assert sig.strategy_version == s.version and len(s.version) == 12
    assert seen > 0
    assert all(s.evaluate(master.all()[0], []) is None for s in APPROVED.values())


# ---- ledger ----
def _trade(side: Side, qty: int, price: str, fees: str = "10", tid: str = "T1") -> BrokerTrade:
    return BrokerTrade(
        trade_id=tid, client_order_id="c", broker_order_id="b", symbol="TEST", side=side, qty=qty, price=D(price), fees=D(fees), ts=T
    )


def test_ledger_pnl_after_fees() -> None:
    lg = Ledger(cash=D(10000))
    lg.apply_entry_fill(_trade(Side.BUY, 10, "100"), stop=D(95), target=D(110), intent_key="k", strategy_id="s", horizon=5)
    assert lg.cash == D(8990) and lg.open_risk() == D(50)
    lg.apply_exit_fill(_trade(Side.SELL, 4, "110", "8", "T2"), "target")
    assert lg.closed[0].pnl_after_fees == D(40) - D(8) - D(4)
    lg.apply_exit_fill(_trade(Side.SELL, 6, "95", "8", "T3"), "stop")
    assert not lg.positions and lg.realized_pnl == D(28) + (D(-30) - D(8) - D(6))
    assert lg.fees_total == D(26)


# ---- reconciliation + stops ----
def test_reconciliation(paper: PaperBroker, store: Store) -> None:
    paper.open_session(T, {"TEST": bar("100", "100", "100", "100", day=date(2026, 11, 2))})
    oms = OrderManager(paper, store, 1, throttle=lambda _: None)
    lg = Ledger(cash=D(10000))
    assert reconcile(paper, lg, oms).ok
    paper.place_order(req("ROGUE", price="95"), T)
    r = reconcile(paper, lg, oms)
    assert not r.ok and "unrecognised" in r.discrepancies[0]
    paper.end_session()
    paper.place_order(req("FILL"), T)
    paper.process_bar(bar("99", "101", "98", "100"), T)
    r = reconcile(paper, lg, oms)
    assert any("cash" in d for d in r.discrepancies) and any("position" in d for d in r.discrepancies)
    paper.faults.unavailable = True
    assert "unreachable" in reconcile(paper, lg, oms).discrepancies[0]


def test_protective_stop_sized_to_filled_qty(paper: PaperBroker, store: Store) -> None:
    paper.open_session(T, {"TEST": bar("100", "100", "100", "100", day=date(2026, 11, 2))})
    oms = OrderManager(paper, store, 1, throttle=lambda _: None)
    lg = Ledger(cash=D(10000))
    master = paper.instruments
    lg.apply_entry_fill(_trade(Side.BUY, 6, "100"), stop=D(95), target=D(110), intent_key="k", strategy_id="s", horizon=5)
    paper.place_order(req("E", qty=6), T)
    paper.process_bar(bar("99", "101", "98", "100"), T)
    seq: dict[str, int] = {}
    assert sync_protection(lg, oms, master, T, seq) == []
    (stop,) = oms.active_orders(Purpose.STOP)
    assert stop.request.qty == 6 and stop.request.trigger_price == D(95) and stop.request.limit_price == stop_limit_price(D(95), D("0.05"))
    assert sync_protection(lg, oms, master, T, seq) == []  # idempotent
    lg.positions["TEST"].qty = 4  # e.g. partial exit
    paper._positions["TEST"].qty = 4
    sync_protection(lg, oms, master, T + timedelta(seconds=5), seq)
    assert stop.state is OrderState.CANCELLED
    (new,) = oms.active_orders(Purpose.STOP)
    assert new.request.qty == 4


def test_unplaceable_stop_is_reported(paper: PaperBroker, store: Store) -> None:
    paper.open_session(T, {"TEST": bar("100", "100", "100", "100", day=date(2026, 11, 2))})
    oms = OrderManager(paper, store, 1, throttle=lambda _: None)
    lg = Ledger(cash=D(10000))
    lg.apply_entry_fill(_trade(Side.BUY, 6, "100"), stop=D(70), target=D(110), intent_key="k", strategy_id="s", horizon=5)
    paper.place_order(req("E", qty=6), T)
    paper.process_bar(bar("99", "101", "98", "100"), T)
    assert sync_protection(lg, oms, paper.instruments, T, {}) == ["TEST"]


# ---- settings / live gate ----
@pytest.mark.parametrize("env", [{"ATA_MARKET_MODE": "live"}, {"ATA_LIVE_TRADING_ENABLED": "true"}, {"ATA_BROKER_COMPLIANCE_GATE": "1"}])
def test_live_cannot_be_enabled(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(LiveTradingDisabled):
        Settings.from_env().assert_paper_only()


def test_default_settings_are_paper(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("ATA_MARKET_MODE", "ATA_LIVE_TRADING_ENABLED", "ATA_BROKER_COMPLIANCE_GATE"):
        monkeypatch.delenv(k, raising=False)
    s = Settings.from_env()
    assert s.market_mode == "paper" and not s.live_trading_enabled
    s.assert_paper_only()


def test_bad_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATA_MARKET_MODE", "turbo")
    with pytest.raises(ValueError):
        Settings.from_env()


# ---- Paytm Money stub ----
def test_paytm_adapter_does_not_fabricate_endpoints() -> None:
    src = inspect.getsource(paytmmoney)
    assert not re.search(r"https?://", src)
    assert not re.search(r"/(api|orders|accounts|login|oauth)[/\"']", src)
    assert "requests" not in src and "httpx" not in src


def test_paytm_adapter_fails_closed() -> None:
    b = paytmmoney.PaytmMoneyBroker()
    assert b.session_valid() is False
    for call in (
        b.available_cash,
        b.positions,
        b.open_orders,
        b.trades,
        lambda: b.order_by_client_id("x"),
        lambda: b.place_order(req(), T),
        lambda: b.cancel_order("x", T),
    ):
        with pytest.raises(LiveTradingDisabled):
            call()


def test_instrument_band() -> None:
    lo, hi = make_instrument(band="0.10").band(D(100))
    assert (lo, hi) == (D(90), D("110.0"))
