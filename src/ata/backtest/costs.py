"""Transaction cost estimates. Rates come from config/costs.yaml and are ESTIMATES
until the broker schedule is verified; nothing here claims to be Paytm Money's fee."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from ata.models import Side
from ata.money import ZERO, D, inr


class CostBreakdown(BaseModel):
    model_config = ConfigDict(frozen=True)

    brokerage: Decimal
    stt: Decimal
    exchange: Decimal
    sebi: Decimal
    stamp: Decimal
    gst: Decimal
    dp: Decimal

    @property
    def total(self) -> Decimal:
        return inr(self.brokerage + self.stt + self.exchange + self.sebi + self.stamp + self.gst + self.dp)


class CostModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    verified: bool
    source: str
    brokerage_per_order_inr: Decimal
    brokerage_pct_cap: Decimal
    stt_delivery_pct: Decimal
    exchange_txn_pct: Decimal
    sebi_fee_pct: Decimal
    stamp_duty_buy_pct: Decimal
    gst_pct: Decimal
    dp_charge_per_sell_inr: Decimal
    slippage_bps: Decimal

    @classmethod
    def load(cls, path: Path) -> CostModel:
        raw = yaml.safe_load(path.read_text())
        return cls.model_validate({k: D(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v for k, v in raw.items()})

    def leg(self, side: Side, qty: int, price: Decimal) -> CostBreakdown:
        value = D(qty) * price
        if qty <= 0:
            return CostBreakdown(brokerage=ZERO, stt=ZERO, exchange=ZERO, sebi=ZERO, stamp=ZERO, gst=ZERO, dp=ZERO)
        brokerage = min(self.brokerage_per_order_inr, self.brokerage_pct_cap * value)
        exchange = self.exchange_txn_pct * value
        sebi = self.sebi_fee_pct * value
        return CostBreakdown(
            brokerage=brokerage,
            stt=self.stt_delivery_pct * value,
            exchange=exchange,
            sebi=sebi,
            stamp=self.stamp_duty_buy_pct * value if side is Side.BUY else ZERO,
            gst=self.gst_pct * (brokerage + exchange + sebi),
            dp=self.dp_charge_per_sell_inr if side is Side.SELL else ZERO,
        )

    def round_trip(self, qty: int, entry: Decimal, exit_: Decimal) -> Decimal:
        """Estimated fees for buy+sell plus slippage on both legs."""
        slip = (self.slippage_bps / Decimal(10000)) * D(qty) * (entry + exit_)
        return inr(self.leg(Side.BUY, qty, entry).total + self.leg(Side.SELL, qty, exit_).total + slip)
