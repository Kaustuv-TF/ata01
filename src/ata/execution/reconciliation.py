"""Broker-vs-internal reconciliation. Any discrepancy halts new entries and alerts."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from ata.brokers.base import Broker, BrokerError
from ata.execution.orders import OrderManager
from ata.money import D
from ata.portfolio.ledger import Ledger

CASH_TOLERANCE = D("1.00")


class ReconResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    discrepancies: tuple[str, ...]
    broker_cash: Decimal | None = None


def reconcile(broker: Broker, ledger: Ledger, oms: OrderManager) -> ReconResult:
    try:
        cash = broker.available_cash()
        remote_pos = broker.positions()
        remote_orders = broker.open_orders()
    except BrokerError as exc:
        return ReconResult(ok=False, discrepancies=(f"broker unreachable: {type(exc).__name__}: {exc}",))
    issues: list[str] = []
    if abs(cash - ledger.cash) > CASH_TOLERANCE:
        issues.append(f"cash: broker {cash} vs ledger {ledger.cash}")
    for sym in sorted(set(remote_pos) | set(ledger.positions)):
        bq = remote_pos[sym].qty if sym in remote_pos else 0
        lq = ledger.positions[sym].qty if sym in ledger.positions else 0
        if bq != lq:
            issues.append(f"position {sym}: broker {bq} vs ledger {lq}")
    known = {o.cid for o in oms.orders.values()}
    for ro in remote_orders:
        if ro.client_order_id not in known:
            issues.append(f"unrecognised open order at broker: {ro.broker_order_id} {ro.symbol}")
    return ReconResult(ok=not issues, discrepancies=tuple(issues), broker_cash=cash)
