from __future__ import annotations

from ata.data import features as f
from ata.data.instruments import Instrument
from ata.models import Bar, Segment, Signal
from ata.strategies.base import Strategy


class Momentum(Strategy):
    """A. Cash-equity trend/momentum: 60d return > 5%, 20d return > 0, above 50d SMA."""

    id = "momentum_v1"
    description = "60-day relative strength with 50-day trend filter, 2xATR stop, 6xATR target"
    min_history = 61

    def evaluate(self, inst: Instrument, bars: list[Bar]) -> Signal | None:
        if inst.segment is not Segment.EQ or len(bars) < self.min_history:
            return None
        r60, r20, sma50, atr = f.ret(bars, 60), f.ret(bars, 20), f.sma(bars, 50), f.atr(bars, 14)
        if None in (r60, r20, sma50, atr):
            return None
        assert r60 is not None and r20 is not None and sma50 is not None and atr is not None
        close = float(bars[-1].close)
        if r60 > 0.05 and r20 > 0 and close > sma50:
            return self.signal(inst, bars[-1].day, close, close - 2 * atr, close + 6 * atr, 20, r60=r60, r20=r20, atr=atr)
        return None
