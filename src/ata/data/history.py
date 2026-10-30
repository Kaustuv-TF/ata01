"""Offline price history: deterministic synthetic bars, or CSV files you supply.

Synthetic data exists so the pipeline runs with no network and no credentials.
It is NOT market data and says nothing about real strategy performance.
"""

from __future__ import annotations

import csv
import math
import random
import zlib
from datetime import date
from pathlib import Path

from ata.data.calendar import MarketCalendar
from ata.data.instruments import Instrument
from ata.models import Bar
from ata.money import D, round_to_tick


def synthetic_bars(inst: Instrument, days: list[date], seed: int) -> list[Bar]:
    rng = random.Random(seed ^ zlib.crc32(inst.symbol.encode()))
    vol = inst.vol_bps / 10000
    drift = inst.drift_bps / 10000
    price = float(inst.start_price)
    bars: list[Bar] = []
    for d in days:
        # regime-ish drift: slow sine on top of the configured drift
        regime = 0.6 * vol * math.sin(len(bars) / 23.0 + seed % 7)
        ret = drift + regime + rng.gauss(0, vol)
        open_ = price * (1 + rng.gauss(0, vol / 3))
        close = max(price * (1 + ret), 0.5)
        hi = max(open_, close) * (1 + abs(rng.gauss(0, vol / 2)))
        lo = min(open_, close) * (1 - abs(rng.gauss(0, vol / 2)))
        t = inst.tick
        o, h, l_, c = (round_to_tick(D(round(x, 4)), t) for x in (open_, hi, lo, close))
        h = max(h, o, c)
        l_ = min(l_, o, c)
        volume = int(inst.volume * (0.6 + rng.random() * 0.8))
        bars.append(Bar(symbol=inst.symbol, day=d, open=o, high=h, low=l_, close=c, volume=volume))
        price = float(c)
    return bars


def synthetic_universe(instruments: list[Instrument], calendar: MarketCalendar, start: date, count: int, seed: int) -> dict[str, list[Bar]]:
    days = calendar.trading_days(start, count)
    return {i.symbol: synthetic_bars(i, days, seed) for i in instruments}


def load_csv(path: Path, symbol: str) -> list[Bar]:
    """CSV with columns date,open,high,low,close,volume (oldest first)."""
    with path.open() as f:
        return [
            Bar(
                symbol=symbol,
                day=date.fromisoformat(r["date"]),
                open=D(r["open"]),
                high=D(r["high"]),
                low=D(r["low"]),
                close=D(r["close"]),
                volume=int(r["volume"]),
                source=f"csv:{path.name}",
            )
            for r in csv.DictReader(f)
        ]
