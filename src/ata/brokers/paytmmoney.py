"""Paytm Money adapter: STUB. Deliberately contains no endpoints, URLs, method
names, auth flow, session duration or fee assumptions.

Public docs (developer.paytmmoney.com, reviewed in the spec on 2026-10-30)
describe REST order placement, order/trade books, positions and historical
data, but do not confirm, for this account: client-direct API eligibility,
static-IP mapping, session lifetime/refresh, rate limits, product/order types,
GTT/stop-loss behaviour, sandbox availability or postbacks. Until the owner
completes onboarding (docs/API_BROKER.md checklist) every method raises.

Next milestone: a READ-ONLY implementation (funds, positions, order book,
trade book) written against the verified official docs or SDK, with each call
cited to the doc page and version it was verified against.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from ata.brokers.base import (
    BrokerOrder,
    BrokerPosition,
    BrokerTrade,
    LiveTradingDisabled,
    OrderRequest,
)

ONBOARDING_REQUIRED = (
    "Paytm Money adapter is not implemented: complete broker API onboarding "
    "(docs/API_BROKER.md) and verify endpoints against official docs first."
)


class PaytmMoneyBroker:
    name = "paytmmoney"

    def __init__(self, *, onboarding_verified: bool = False) -> None:
        # Credentials are never accepted here; they must come from a secret
        # manager inside the isolated execution service (future milestone).
        self.onboarding_verified = onboarding_verified

    def _unavailable(self) -> LiveTradingDisabled:
        return LiveTradingDisabled(ONBOARDING_REQUIRED)

    def session_valid(self) -> bool:
        return False  # fail closed: an unverified session is an invalid session

    def available_cash(self) -> Decimal:
        raise self._unavailable()

    def positions(self) -> dict[str, BrokerPosition]:
        raise self._unavailable()

    def open_orders(self) -> list[BrokerOrder]:
        raise self._unavailable()

    def place_order(self, req: OrderRequest, now: datetime) -> str:
        raise self._unavailable()

    def cancel_order(self, broker_order_id: str, now: datetime) -> None:
        raise self._unavailable()

    def order_by_client_id(self, client_order_id: str) -> BrokerOrder | None:
        raise self._unavailable()

    def trades(self) -> list[BrokerTrade]:
        raise self._unavailable()
