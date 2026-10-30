"""Protective stops: after any fill, each position must carry exactly one stop-limit
sized to the confirmed filled quantity. A stop is protection, not a guaranteed exit."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from ata.brokers.base import OrderRequest
from ata.data.instruments import InstrumentMaster
from ata.execution.orders import OrderManager, OrderState, Purpose, client_order_id
from ata.models import OrderType, Product, Side
from ata.money import D, floor_to_tick
from ata.portfolio.ledger import Ledger

STOP_LIMIT_BUFFER = D("0.01")  # limit 1% below trigger


def stop_limit_price(trigger: Decimal, tick: Decimal) -> Decimal:
    return floor_to_tick(trigger * (1 - STOP_LIMIT_BUFFER), tick)


def sync_protection(ledger: Ledger, oms: OrderManager, instruments: InstrumentMaster, now: datetime, seq: dict[str, int]) -> list[str]:
    """Ensure stop qty == position qty. Returns symbols left unprotected (alert + exit)."""
    unprotected: list[str] = []
    for sym, pos in ledger.positions.items():
        if oms.active_orders(Purpose.EXIT, sym):
            continue  # exit in flight; stop was deliberately cancelled
        stops = oms.active_orders(Purpose.STOP, sym)
        if len(stops) == 1 and stops[0].request.qty - stops[0].filled_qty == pos.qty:
            continue
        for s in stops:
            oms.cancel(s, now)
        if any(o.active for o in stops):
            unprotected.append(sym)
            continue
        inst = instruments.get(sym)
        assert inst is not None
        seq[sym] = seq.get(sym, 0) + 1
        req = OrderRequest(
            client_order_id=client_order_id(pos.intent_key, Purpose.STOP, seq[sym]),
            symbol=sym,
            exchange=inst.exchange,
            segment=inst.segment,
            product=Product.DELIVERY,
            side=Side.SELL,
            order_type=OrderType.STOP_LIMIT,
            qty=pos.qty,
            trigger_price=pos.stop_price,
            limit_price=stop_limit_price(pos.stop_price, inst.tick),
        )
        order = oms.create(req, pos.intent_key, Purpose.STOP, now)
        oms.transition(order, OrderState.RISK_APPROVED, now, "protective stop (risk-reducing)")
        oms.submit(order, now)
        if order.state not in (OrderState.ACKNOWLEDGED, OrderState.PART_FILLED, OrderState.FILLED):
            unprotected.append(sym)
    return unprotected
