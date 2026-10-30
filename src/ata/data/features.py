"""Deterministic technical features over a bar history (most recent bar last)."""

from __future__ import annotations

from ata.models import Bar


def closes(bars: list[Bar]) -> list[float]:
    return [float(b.close) for b in bars]


def sma(bars: list[Bar], n: int) -> float | None:
    if len(bars) < n:
        return None
    return sum(closes(bars[-n:])) / n


def ret(bars: list[Bar], n: int) -> float | None:
    if len(bars) <= n:
        return None
    return float(bars[-1].close) / float(bars[-1 - n].close) - 1


def atr(bars: list[Bar], n: int = 14) -> float | None:
    if len(bars) <= n:
        return None
    trs = []
    for prev, cur in zip(bars[-n - 1 : -1], bars[-n:], strict=True):
        trs.append(max(float(cur.high - cur.low), abs(float(cur.high - prev.close)), abs(float(cur.low - prev.close))))
    return sum(trs) / n


def highest_high(bars: list[Bar], n: int) -> float | None:
    if len(bars) < n:
        return None
    return max(float(b.high) for b in bars[-n:])


def zscore_vs_sma(bars: list[Bar], n: int) -> float | None:
    if len(bars) < n:
        return None
    window = closes(bars[-n:])
    mean = sum(window) / n
    var = sum((x - mean) ** 2 for x in window) / n
    return 0.0 if var == 0 else (window[-1] - mean) / var**0.5
