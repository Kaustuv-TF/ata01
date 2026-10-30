"""NSE session calendar in Asia/Kolkata. Holiday list is configuration and must be verified yearly."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

IST = ZoneInfo("Asia/Kolkata")


class MarketCalendar:
    def __init__(self, holidays: set[date], open_: time = time(9, 15), close: time = time(15, 30), verified: bool = False) -> None:
        self.holidays = holidays
        self.open = open_
        self.close = close
        self.verified = verified

    @classmethod
    def load(cls, path: Path) -> MarketCalendar:
        raw = yaml.safe_load(path.read_text())
        holidays = {d if isinstance(d, date) else date.fromisoformat(d) for d in raw.get("holidays", [])}
        s = raw["session"]
        return cls(holidays, time.fromisoformat(s["open"]), time.fromisoformat(s["close"]), bool(raw.get("holidays_verified")))

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def is_open(self, ts: datetime) -> bool:
        local = ts.astimezone(IST)
        return self.is_trading_day(local.date()) and self.open <= local.time() < self.close

    def next_trading_day(self, d: date) -> date:
        d += timedelta(days=1)
        while not self.is_trading_day(d):
            d += timedelta(days=1)
        return d

    def trading_days(self, start: date, count: int) -> list[date]:
        days: list[date] = []
        d = start if self.is_trading_day(start) else self.next_trading_day(start)
        while len(days) < count:
            days.append(d)
            d = self.next_trading_day(d)
        return days

    def session_ts(self, d: date, hh: int, mm: int) -> datetime:
        return datetime(d.year, d.month, d.day, hh, mm, tzinfo=IST)
