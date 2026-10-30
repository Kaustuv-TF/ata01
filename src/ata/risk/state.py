"""Persistent risk state: high-water mark, daily freeze, drawdown lockout, kill switch.

The drawdown lockout and kill switch survive process restarts and can only be
cleared through `owner_clear`, which requires a named human author and reason
and is written to control_events. No code path from the AI layer reaches it.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from ata.money import D
from ata.ops.store import Store
from ata.risk.limits import RiskLimits

OWNER_CLEARABLE = {"drawdown_lockout", "kill_switch"}
AI_AUTHORS = {"ai", "llm", "model", "agent", "system"}


class RiskState:
    def __init__(self, store: Store, limits: RiskLimits) -> None:
        self.store = store
        self.limits = limits
        if self.store.get_state("high_water_mark") is None:
            self.store.set_state("high_water_mark", str(limits.initial_capital_inr))

    # --- reads ---
    @property
    def high_water_mark(self) -> Decimal:
        return D(self.store.get_state("high_water_mark"))

    def lockout(self) -> dict[str, str] | None:
        result: dict[str, str] | None = self.store.get_state("drawdown_lockout")
        return result

    def kill_switch(self) -> dict[str, str] | None:
        result: dict[str, str] | None = self.store.get_state("kill_switch")
        return result

    def daily_freeze(self, day: date) -> dict[str, str] | None:
        freeze = self.store.get_state("daily_freeze")
        return freeze if freeze and freeze.get("day") == day.isoformat() else None

    def day_start_equity(self, day: date) -> Decimal | None:
        rec = self.store.get_state("day_start")
        return D(rec["equity"]) if rec and rec.get("day") == day.isoformat() else None

    # --- updates driven by marked equity ---
    def start_day(self, day: date, equity: Decimal) -> None:
        """Daily threshold resets each trading day, based on current equity."""
        if self.day_start_equity(day) is None:
            self.store.set_state("day_start", {"day": day.isoformat(), "equity": str(equity)})

    def on_equity(self, day: date, equity: Decimal, ts: datetime) -> list[str]:
        """Update HWM and evaluate daily freeze / drawdown lockout. Returns newly triggered events."""
        events: list[str] = []
        if equity > self.high_water_mark:
            self.store.set_state("high_water_mark", str(equity))
        start = self.day_start_equity(day)
        if start is not None and not self.daily_freeze(day) and start - equity >= self.limits.daily_loss_freeze(start):
            self.store.set_state(
                "daily_freeze",
                {"day": day.isoformat(), "reason": f"daily loss {start - equity} >= {self.limits.daily_loss_freeze(start)}"},
            )
            self.store.audit("risk", "daily_freeze", {"day": day, "start": start, "equity": equity}, ts)
            events.append("daily_freeze")
        hwm = self.high_water_mark
        if not self.lockout() and hwm - equity >= self.limits.drawdown_lockout(hwm):
            self.store.set_state(
                "drawdown_lockout",
                {"since": ts.isoformat(), "reason": f"drawdown {hwm - equity} from HWM {hwm} >= {self.limits.drawdown_lockout(hwm)}"},
            )
            self.store.audit("risk", "drawdown_lockout", {"hwm": hwm, "equity": equity}, ts)
            self.store.control_event("drawdown_lockout", "risk-engine", f"equity {equity} vs HWM {hwm}", ts)
            events.append("drawdown_lockout")
        return events

    def engage_kill_switch(self, author: str, reason: str, ts: datetime) -> None:
        self.store.set_state("kill_switch", {"since": ts.isoformat(), "author": author, "reason": reason})
        self.store.control_event("kill_switch_engaged", author, reason, ts)
        self.store.audit("risk", "kill_switch_engaged", {"author": author, "reason": reason}, ts)

    def owner_clear(self, key: str, author: str, reason: str, ts: datetime, current_equity: Decimal) -> None:
        """Human-only clear of a persistent block. Rebases HWM to current equity for a lockout."""
        if key not in OWNER_CLEARABLE:
            raise ValueError(f"{key} is not owner-clearable")
        if not author.strip() or author.strip().lower() in AI_AUTHORS:
            raise PermissionError("a named human owner must clear persistent risk blocks")
        if len(reason.strip()) < 10:
            raise PermissionError("a written reason (>=10 chars) is required")
        self.store.set_state(key, None)
        if key == "drawdown_lockout":
            self.store.set_state("high_water_mark", str(current_equity))
        self.store.control_event(f"{key}_cleared", author, reason, ts)
        self.store.audit("risk", f"{key}_cleared", {"author": author, "reason": reason, "equity": current_equity}, ts)
