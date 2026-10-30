"""Instrument master. Offline sample from config/instruments.yaml; live master must come from broker."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from ata.models import Exchange, InstrumentStatus, Segment
from ata.money import D


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str
    exchange: Exchange
    segment: Segment
    tick: Decimal
    lot: int
    status: InstrumentStatus
    band_pct: Decimal
    asof: date | None = None
    # sample-data generation parameters (synthetic only)
    start_price: Decimal = D(100)
    vol_bps: int = 100
    drift_bps: int = 0
    volume: int = 100000

    def band(self, prev_close: Decimal) -> tuple[Decimal, Decimal]:
        return prev_close * (1 - self.band_pct), prev_close * (1 + self.band_pct)


class InstrumentMaster:
    def __init__(self, instruments: list[Instrument]) -> None:
        self._by_symbol = {i.symbol: i for i in instruments}

    @classmethod
    def load(cls, path: Path) -> InstrumentMaster:
        raw = yaml.safe_load(path.read_text())
        return cls([Instrument.model_validate(i) for i in raw["instruments"]])

    def get(self, symbol: str) -> Instrument | None:
        return self._by_symbol.get(symbol)

    def symbols(self) -> list[str]:
        return sorted(self._by_symbol)

    def all(self) -> list[Instrument]:
        return [self._by_symbol[s] for s in self.symbols()]
