"""Strategy interface. Strategies are deterministic, versioned by source hash, and
can only emit long cash-equity/ETF entry candidates. The AI layer cannot add strategies."""

from __future__ import annotations

import hashlib
import inspect
from abc import ABC, abstractmethod
from datetime import date

from ata.data.instruments import Instrument
from ata.models import Bar, Side, Signal
from ata.money import D, ceil_to_tick, floor_to_tick


class Strategy(ABC):
    id: str
    description: str
    min_history: int

    @property
    def version(self) -> str:
        return hashlib.sha256(inspect.getsource(type(self)).encode()).hexdigest()[:12]

    @abstractmethod
    def evaluate(self, inst: Instrument, bars: list[Bar]) -> Signal | None: ...

    def signal(self, inst: Instrument, day: date, ref: float, stop: float, target: float, horizon: int, **features: float) -> Signal:
        return Signal(
            id=f"{self.id}:{inst.symbol}:{day.isoformat()}",
            strategy_id=self.id,
            strategy_version=self.version,
            day=day,
            symbol=inst.symbol,
            side=Side.BUY,
            reference_price=D(ref),
            stop_price=floor_to_tick(D(round(stop, 4)), inst.tick),
            target_price=ceil_to_tick(D(round(target, 4)), inst.tick),
            horizon_days=horizon,
            features={k: f"{v:.6f}" for k, v in features.items()},
        )
