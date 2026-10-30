from __future__ import annotations

from ata.data import features as f
from ata.data.instruments import Instrument
from ata.models import Bar, Segment, Signal
from ata.strategies.base import Strategy


class VolatilityBreakout(Strategy):
    """C. Close above prior 20-day high; ATR-based stop and target."""

    id = "breakout_v1"
    description = "20-day high breakout, 1.5xATR stop, 4.5xATR target"
    min_history = 21

    def evaluate(self, inst: Instrument, bars: list[Bar]) -> Signal | None:
        if inst.segment is not Segment.EQ or len(bars) < self.min_history:
            return None
        prior_high, atr = f.highest_high(bars[:-1], 20), f.atr(bars, 14)
        if prior_high is None or atr is None:
            return None
        close = float(bars[-1].close)
        if close > prior_high:
            return self.signal(inst, bars[-1].day, close, close - 1.5 * atr, close + 4.5 * atr, 10, prior_high=prior_high, atr=atr)
        return None
