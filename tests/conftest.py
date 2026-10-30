from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from ata.backtest.costs import CostModel
from ata.brokers.paper import PaperBroker
from ata.data.calendar import IST, MarketCalendar
from ata.data.instruments import Instrument, InstrumentMaster
from ata.data.validators import OK
from ata.models import Exchange, InstrumentStatus, Quote, Segment, Side, TradeIntent
from ata.money import D
from ata.ops.store import Store
from ata.risk.limits import RiskLimits, load_limits
from ata.risk.rules import GateContext, PortfolioView, RiskGateway
from ata.settings import ROOT, Settings

CONFIG = ROOT / "config"
DAY = date(2026, 11, 3)  # Tuesday
NOW = datetime(2026, 11, 3, 9, 20, tzinfo=IST)


@pytest.fixture
def limits() -> RiskLimits:
    return load_limits(CONFIG / "defaults.yaml")[0]


@pytest.fixture
def costs() -> CostModel:
    return CostModel.load(CONFIG / "costs.yaml")


@pytest.fixture
def zero_costs(costs: CostModel) -> CostModel:
    return costs.model_copy(
        update={
            k: D(0)
            for k in (
                "brokerage_per_order_inr",
                "stt_delivery_pct",
                "exchange_txn_pct",
                "sebi_fee_pct",
                "stamp_duty_buy_pct",
                "gst_pct",
                "dp_charge_per_sell_inr",
                "slippage_bps",
            )
        }
    )


@pytest.fixture
def calendar() -> MarketCalendar:
    return MarketCalendar.load(CONFIG / "markets.yaml")


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(database_url=f"sqlite:///{tmp_path / 'engine.db'}")


def make_instrument(
    symbol: str = "TEST",
    *,
    tick: str = "0.05",
    lot: int = 1,
    status: InstrumentStatus = InstrumentStatus.ACTIVE,
    segment: Segment = Segment.EQ,
    band: str = "0.20",
) -> Instrument:
    return Instrument(symbol=symbol, exchange=Exchange.NSE, segment=segment, tick=D(tick), lot=lot, status=status, band_pct=D(band))


@pytest.fixture
def instruments() -> InstrumentMaster:
    return InstrumentMaster([make_instrument("TEST"), make_instrument("ASMX", status=InstrumentStatus.ASM), make_instrument("LOT5", lot=5)])


def make_quote(price: str | Decimal = "100", spread_bps: int = 10, ts: datetime = NOW, symbol: str = "TEST") -> Quote:
    p = D(price)
    half = p * spread_bps / D(20000)
    return Quote(symbol=symbol, ts=ts, bid=p - half, ask=p + half, last=p, prev_close=p)


def make_intent(
    symbol: str = "TEST",
    ref: str = "100",
    stop: str = "98",
    target: str = "110",
    key: str = "k1",
    side: Side = Side.BUY,
    segment: Segment = Segment.EQ,
) -> TradeIntent:
    return TradeIntent(
        unique_key=key,
        decision_id="d1",
        strategy_id="momentum_v1",
        symbol=symbol,
        exchange=Exchange.NSE,
        segment=segment,
        side=side,
        reference_price=D(ref),
        stop_price=D(stop),
        target_price=D(target),
        horizon_days=10,
        confidence=D("0.5"),
        evidence_ids=("e1",),
    )


def empty_view(equity: str = "10000", cash: str | None = None) -> PortfolioView:
    return PortfolioView(equity=D(equity), cash=D(cash or equity), position_cost={}, position_marks={}, open_risk=D(0))


_DEFAULT: object = object()


def make_ctx(instrument: object = _DEFAULT, quote: object = _DEFAULT, **overrides: object) -> GateContext:
    base: dict[str, object] = dict(
        now=NOW,
        day=DAY,
        market_open=True,
        trading_day=True,
        live_mode=False,
        broker_session_valid=True,
        reconciled=True,
        entries_halted=(),
        kill_switch=False,
        drawdown_lockout=False,
        daily_freeze=False,
        data_check=OK,
        quote=make_quote() if quote is _DEFAULT else quote,
        prev_volume=1_000_000,
        instrument=make_instrument() if instrument is _DEFAULT else instrument,
    )
    base.update(overrides)
    return GateContext(**base)  # type: ignore[arg-type]


def gateway(limits: RiskLimits, costs: CostModel) -> RiskGateway:
    return RiskGateway(limits, costs)


@pytest.fixture
def paper(instruments: InstrumentMaster, calendar: MarketCalendar, costs: CostModel) -> PaperBroker:
    return PaperBroker(instruments, calendar, costs, cash=D(10000))
