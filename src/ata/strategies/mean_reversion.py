from __future__ import annotations

from ata.data import features as f
from ata.data.instruments import Instrument
from ata.models import Bar, Segment, Signal
from ata.strategies.base import Strategy


class MeanReversion(Strategy):
    """B. Short-horizon mean reversion, only inside an uptrend (close > 100d SMA)."""

    id = "mean_reversion_v1"
    description = "10-day z-score < -1.5 within 100-day uptrend, target 10-day mean"
    min_history = 100

    def evaluate(self, inst: Instrument, bars: list[Bar]) -> Signal | None:
        if inst.segment is not Segment.EQ or len(bars) < self.min_history:
            return None
        z, sma10, sma100, atr = f.zscore_vs_sma(bars, 10), f.sma(bars, 10), f.sma(bars, 100), f.atr(bars, 14)
        if None in (z, sma10, sma100, atr):
            return None
        assert z is not None and sma10 is not None and sma100 is not None and atr is not None
        close = float(bars[-1].close)
        if z < -1.5 and close > sma100 and sma10 > close:
            return self.signal(inst, bars[-1].day, close, close - 1.5 * atr, sma10, 5, z=z, atr=atr)
        return None
