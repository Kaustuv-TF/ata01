"""Market-data quality gates. Any failure means: no new risk on that symbol (fail closed)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from ata.models import Bar, Quote


class DataCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    reason: str = ""


OK = DataCheck(ok=True)


def validate_bar(bar: Bar, prev: Bar | None = None) -> DataCheck:
    if min(bar.open, bar.high, bar.low, bar.close) <= 0:
        return DataCheck(ok=False, reason="non-positive price")
    if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close) or bar.low > bar.high:
        return DataCheck(ok=False, reason="inconsistent OHLC")
    if bar.volume < 0:
        return DataCheck(ok=False, reason="negative volume")
    if prev is not None and bar.day <= prev.day:
        return DataCheck(ok=False, reason="non-increasing bar dates")
    return OK


def validate_history(bars: list[Bar]) -> DataCheck:
    prev: Bar | None = None
    for bar in bars:
        check = validate_bar(bar, prev)
        if not check.ok:
            return DataCheck(ok=False, reason=f"{bar.symbol} {bar.day}: {check.reason}")
        prev = bar
    return OK


def validate_quote(quote: Quote, now: datetime, max_age: timedelta, max_spread_bps: Decimal) -> DataCheck:
    if quote.ts > now + timedelta(seconds=5):
        return DataCheck(ok=False, reason="quote timestamp in the future")
    if now - quote.ts > max_age:
        return DataCheck(ok=False, reason=f"stale quote ({now - quote.ts} old)")
    if quote.bid <= 0 or quote.ask <= 0:
        return DataCheck(ok=False, reason="non-positive bid/ask")
    if quote.bid > quote.ask:
        return DataCheck(ok=False, reason="crossed quote")
    if quote.spread_bps > max_spread_bps:
        return DataCheck(ok=False, reason=f"spread {quote.spread_bps:.1f}bps > {max_spread_bps}bps")
    return OK
