"""Broker interface. ATA never calls a broker except through this protocol, and
only the execution layer holds a broker instance (the AI layer never does)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from ata.models import Exchange, OrderType, Product, Segment, Side
from ata.money import ZERO


class BrokerError(Exception):
    """Base broker failure."""


class BrokerTimeout(BrokerError):
    """No response: the order may or may not exist at the broker. Reconcile, never resubmit."""


class BrokerUnavailable(BrokerError):
    """Connectivity lost / broker down."""


class BrokerRejected(BrokerError):
    """Definitive rejection from broker/exchange."""


class SessionExpired(BrokerError):
    """Auth/session no longer valid. A human must re-authenticate; ATA never bypasses 2FA."""


class LiveTradingDisabled(BrokerError):
    """Raised by any live adapter path while live trading is not authorised."""


class BrokerOrderStatus(StrEnum):
    OPEN = "OPEN"
    TRIGGER_PENDING = "TRIGGER_PENDING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class OrderRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    client_order_id: str
    symbol: str
    exchange: Exchange
    segment: Segment
    product: Product
    side: Side
    order_type: OrderType
    qty: int
    limit_price: Decimal
    trigger_price: Decimal | None = None


class BrokerOrder(BaseModel):
    client_order_id: str
    broker_order_id: str
    symbol: str
    side: Side
    order_type: OrderType
    qty: int
    filled_qty: int = 0
    avg_fill_price: Decimal = ZERO
    status: BrokerOrderStatus
    reason: str = ""
    limit_price: Decimal
    trigger_price: Decimal | None = None


class BrokerTrade(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_id: str
    client_order_id: str
    broker_order_id: str
    symbol: str
    side: Side
    qty: int
    price: Decimal
    fees: Decimal
    ts: datetime


class BrokerPosition(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    qty: int
    avg_price: Decimal


class Broker(Protocol):
    name: str

    def session_valid(self) -> bool: ...
    def available_cash(self) -> Decimal: ...
    def positions(self) -> dict[str, BrokerPosition]: ...
    def open_orders(self) -> list[BrokerOrder]: ...
    def place_order(self, req: OrderRequest, now: datetime) -> str: ...
    def cancel_order(self, broker_order_id: str, now: datetime) -> None: ...
    def order_by_client_id(self, client_order_id: str) -> BrokerOrder | None: ...
    def trades(self) -> list[BrokerTrade]: ...
