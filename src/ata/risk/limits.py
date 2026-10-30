"""Immutable risk limits for the INR 10,000 mandate.

MANDATE_CEILINGS is the owner-approved ceiling set (spec V1.1 section 1A). The
YAML policy may only tighten these. Nothing at runtime (AI output, CLI flags,
restarts) can loosen them; raising a ceiling is a code change under owner
change-control.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ata.models import Product, Segment
from ata.money import D

MANDATE_CEILINGS: dict[str, Decimal] = {
    "initial_capital_inr": D(10000),
    "bot_capital_cap_inr": D(10000),
    "per_trade_risk_inr": D(50),
    "per_trade_risk_pct": D("0.005"),
    "aggregate_risk_inr": D(100),
    "aggregate_risk_pct": D("0.01"),
    "per_symbol_cap_inr": D(2500),
    "gross_invested_cap_inr": D(7500),
    "max_positions": D(3),
    "daily_loss_freeze_inr": D(150),
    "daily_loss_freeze_pct": D("0.015"),
    "drawdown_lockout_inr": D(500),
    "drawdown_lockout_pct": D("0.05"),
    "max_orders_per_second": D(1),
}
# Floors: values that may only be raised (more conservative when larger).
MANDATE_FLOORS: dict[str, Decimal] = {
    "min_cash_buffer_inr": D(2500),
}
MANDATE_SEGMENTS = frozenset({Segment.EQ, Segment.ETF})
MANDATE_PRODUCTS = frozenset({Product.DELIVERY})


class LimitViolation(Exception):
    """Raised when a policy tries to loosen a mandate limit."""


class RiskLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    initial_capital_inr: Decimal
    bot_capital_cap_inr: Decimal
    per_trade_risk_inr: Decimal
    per_trade_risk_pct: Decimal
    aggregate_risk_inr: Decimal
    aggregate_risk_pct: Decimal
    per_symbol_cap_inr: Decimal
    gross_invested_cap_inr: Decimal
    min_cash_buffer_inr: Decimal
    max_positions: int
    daily_loss_freeze_inr: Decimal
    daily_loss_freeze_pct: Decimal
    drawdown_lockout_inr: Decimal
    drawdown_lockout_pct: Decimal
    max_orders_per_second: int
    max_spread_bps: Decimal
    max_stop_distance_pct: Decimal
    min_edge_to_cost_ratio: Decimal
    max_participation_pct: Decimal
    max_data_age_minutes: int
    permitted_segments: frozenset[Segment]
    permitted_products: frozenset[Product]

    @field_validator("*", mode="before")
    @classmethod
    def _no_floats(cls, v: Any) -> Any:
        return D(v) if isinstance(v, float) else v

    @model_validator(mode="after")
    def _enforce_mandate(self) -> RiskLimits:
        for name, ceiling in MANDATE_CEILINGS.items():
            value = D(getattr(self, name))
            if value > ceiling:
                raise LimitViolation(f"{name}={value} exceeds mandate ceiling {ceiling}")
            if value <= 0:
                raise LimitViolation(f"{name} must be positive")
        for name, floor in MANDATE_FLOORS.items():
            if D(getattr(self, name)) < floor:
                raise LimitViolation(f"{name} below mandate floor {floor}")
        if not self.permitted_segments <= MANDATE_SEGMENTS:
            raise LimitViolation(f"segments {set(self.permitted_segments) - MANDATE_SEGMENTS} not in mandate")
        if not self.permitted_products <= MANDATE_PRODUCTS:
            raise LimitViolation(f"products {set(self.permitted_products) - MANDATE_PRODUCTS} not in mandate")
        if self.gross_invested_cap_inr + self.min_cash_buffer_inr > self.bot_capital_cap_inr:
            raise LimitViolation("gross cap + cash buffer exceeds bot capital cap")
        return self

    # Effective limits shrink with equity (percent of current equity) but never exceed the INR cap.
    def per_trade_risk(self, equity: Decimal) -> Decimal:
        return min(self.per_trade_risk_inr, self.per_trade_risk_pct * equity)

    def aggregate_risk(self, equity: Decimal) -> Decimal:
        return min(self.aggregate_risk_inr, self.aggregate_risk_pct * equity)

    def daily_loss_freeze(self, day_start_equity: Decimal) -> Decimal:
        return min(self.daily_loss_freeze_inr, self.daily_loss_freeze_pct * day_start_equity)

    def drawdown_lockout(self, high_water_mark: Decimal) -> Decimal:
        return min(self.drawdown_lockout_inr, self.drawdown_lockout_pct * high_water_mark)

    def policy_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()


def load_limits(path: Path) -> tuple[RiskLimits, str]:
    """Load and validate the risk policy. Returns (limits, policy_version)."""
    raw = yaml.safe_load(path.read_text())
    risk = raw["risk"]
    limits = RiskLimits.model_validate(
        {
            k: (D(v) if isinstance(v, (int, float)) and k not in {"max_positions", "max_orders_per_second", "max_data_age_minutes"} else v)
            for k, v in risk.items()
        }
    )
    return limits, str(raw.get("policy_version", "unversioned"))
