"""Alert sink. Offline: collected + audited + printed. Telegram/email transports are a later milestone."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ata.ops.store import Store


@dataclass
class Alert:
    ts: datetime
    severity: str  # INFO | WARN | CRITICAL
    message: str


@dataclass
class AlertSink:
    store: Store
    echo: bool = False
    alerts: list[Alert] = field(default_factory=list)

    def emit(self, severity: str, message: str, ts: datetime) -> None:
        self.alerts.append(Alert(ts, severity, message))
        self.store.audit("monitor", "alert", {"severity": severity, "message": message}, ts)
        if self.echo:
            print(f"  [{severity}] {ts:%Y-%m-%d %H:%M} {message}")
