"""Strict JSON contracts between ATA and the model (spec section 5).

The model only ever sees an AISnapshot (no balances, credentials, limits or
order controls) and may only return an AIDecision. Unknown fields are rejected,
so a response that tries to set risk limits, permissions or order instructions
fails validation outright.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ata.models import Exchange, Segment, Side

SNAPSHOT_VERSION: Literal["ata.snapshot.v1"] = "ata.snapshot.v1"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---- input to the model (sanitized, read-only) ----
class StrategyInfo(Strict):
    id: str
    version: str
    description: str


class Candidate(Strict):
    evidence_id: str
    strategy_id: str
    symbol: str
    exchange: Exchange
    segment: Segment
    reference_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    horizon_days: int
    features: dict[str, str]


class AISnapshot(Strict):
    snapshot_version: Literal["ata.snapshot.v1"] = SNAPSHOT_VERSION
    timestamp: datetime
    data_as_of: datetime
    data_fresh: bool
    approved_strategies: list[StrategyInfo]
    regime_factors: dict[str, str]
    candidates: list[Candidate]


# ---- output from the model ----
class StrategyWeight(Strict):
    id: str
    weight: Decimal = Field(ge=0, le=1)
    justification: str = Field(max_length=500)


class AITradeIntent(Strict):
    symbol: str
    exchange: Exchange
    segment: Segment
    side: Side
    reference_price: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    target_price: Decimal = Field(gt=0)
    horizon: int = Field(ge=1, le=60)
    confidence: Decimal = Field(ge=0, le=1)  # not assumed calibrated
    evidence_ids: list[str] = Field(min_length=1, max_length=10)


class AIDecision(Strict):
    timestamp: datetime
    model_version: str = Field(min_length=1, max_length=100)
    market_regime: Literal["trending_up", "trending_down", "range_bound", "volatile", "unknown"]
    strategies: list[StrategyWeight] = Field(max_length=20)
    trade_intents: list[AITradeIntent] = Field(max_length=10)
    warnings: list[str] = Field(default_factory=list, max_length=20)
