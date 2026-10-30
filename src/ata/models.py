"""Domain types shared across modules."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Exchange(StrEnum):
    NSE = "NSE"
    BSE = "BSE"


class Segment(StrEnum):
    """Only cash equity and ETFs are permitted in V1; others exist so they can be rejected explicitly."""

    EQ = "EQ"
    ETF = "ETF"
    FUT = "FUT"
    OPT = "OPT"


class Product(StrEnum):
    DELIVERY = "DELIVERY"  # cash-and-carry, no leverage
    INTRADAY = "INTRADAY"
    MARGIN = "MARGIN"


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    STOP_LIMIT = "STOP_LIMIT"
    MARKET = "MARKET"  # never used by ATA; present so it can be rejected


class InstrumentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ASM = "ASM"
    GSM = "GSM"
    RESTRICTED = "RESTRICTED"


class Bar(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    source: str = "synthetic"


class Quote(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    ts: datetime
    bid: Decimal
    ask: Decimal
    last: Decimal
    prev_close: Decimal

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> Decimal:
        return (self.ask - self.bid) / self.mid * Decimal(10000)


class Signal(BaseModel):
    """Deterministic strategy output; the AI layer may only rank/weight these."""

    model_config = ConfigDict(frozen=True)

    id: str
    strategy_id: str
    strategy_version: str
    day: date
    symbol: str
    side: Side
    reference_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    horizon_days: int
    features: dict[str, str] = Field(default_factory=dict)


class TradeIntent(BaseModel):
    """A validated, AI-ranked entry candidate. Immutable once created."""

    model_config = ConfigDict(frozen=True)

    unique_key: str
    decision_id: str
    strategy_id: str
    symbol: str
    exchange: Exchange
    segment: Segment
    side: Side
    reference_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    horizon_days: int
    confidence: Decimal
    evidence_ids: tuple[str, ...]
    product: Product = Product.DELIVERY
