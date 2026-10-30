from __future__ import annotations

from ata.data import features as f
from ata.data.instruments import Instrument
from ata.models import Bar, Segment, Signal
from ata.strategies.base import Strategy


class EtfTrend(Strategy):
    """D. Liquid ETF trend: positive 60d return and above 50d SMA."""

    id = "etf_trend_v1"
    description = "ETF 60-day trend with 50-day filter, 2xATR stop, 6xATR target"
    min_history = 61

    def evaluate(self, inst: Instrument, bars: list[Bar]) -> Signal | None:
        if inst.segment is not Segment.ETF or len(bars) < self.min_history:
            return None
        r60, sma50, atr = f.ret(bars, 60), f.sma(bars, 50), f.atr(bars, 14)
        if r60 is None or sma50 is None or atr is None:
            return None
        close = float(bars[-1].close)
        if r60 > 0.02 and close > sma50:
            return self.signal(inst, bars[-1].day, close, close - 2 * atr, close + 6 * atr, 20, r60=r60, atr=atr)
        return None
