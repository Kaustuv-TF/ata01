"""Persistence: append-only hash-chained audit log, risk state and control events.

SQLite by default for offline runs; set ATA_DATABASE_URL to a Postgres URL
(postgresql+psycopg://...) for deployed paper runs.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

audit_log = Table(
    "audit_log",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("component", String(64), nullable=False),
    Column("action", String(64), nullable=False),
    Column("payload", Text, nullable=False),
    Column("prev_checksum", String(64), nullable=False),
    Column("checksum", String(64), nullable=False),
)

risk_state = Table(
    "risk_state",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

control_events = Table(
    "control_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("type", String(64), nullable=False),
    Column("author", String(128), nullable=False),
    Column("reason", Text, nullable=False),
)

GENESIS = "0" * 64


def _checksum(prev: str, ts: datetime, component: str, action: str, payload: str) -> str:
    return hashlib.sha256(f"{prev}|{ts.isoformat()}|{component}|{action}|{payload}".encode()).hexdigest()


class Store:
    def __init__(self, url: str) -> None:
        self.engine: Engine = create_engine(url, future=True)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def _pragma(dbapi_conn: Any, _: Any) -> None:
                dbapi_conn.execute("PRAGMA journal_mode=WAL")

        metadata.create_all(self.engine)

    # --- audit log (append-only) ---
    def audit(self, component: str, action: str, payload: dict[str, Any], ts: datetime | None = None) -> str:
        ts = (ts or datetime.now(UTC)).astimezone(UTC)  # normalise: SQLite drops tz info
        body = json.dumps(payload, sort_keys=True, default=str)
        with self.engine.begin() as conn:
            last = conn.execute(select(audit_log.c.checksum).order_by(audit_log.c.seq.desc()).limit(1)).scalar()
            prev = last or GENESIS
            checksum = _checksum(prev, ts, component, action, body)
            conn.execute(
                insert(audit_log).values(ts=ts, component=component, action=action, payload=body, prev_checksum=prev, checksum=checksum)
            )
        return checksum

    def audit_rows(self, action: str | None = None) -> list[dict[str, Any]]:
        stmt = select(audit_log).order_by(audit_log.c.seq)
        if action:
            stmt = stmt.where(audit_log.c.action == action)
        with self.engine.connect() as conn:
            return [{**row._asdict(), "payload": json.loads(row.payload)} for row in conn.execute(stmt)]

    def verify_audit_chain(self) -> bool:
        prev = GENESIS
        with self.engine.connect() as conn:
            for row in conn.execute(select(audit_log).order_by(audit_log.c.seq)):
                ts = row.ts if row.ts.tzinfo else row.ts.replace(tzinfo=UTC)
                if row.prev_checksum != prev or row.checksum != _checksum(prev, ts, row.component, row.action, row.payload):
                    return False
                prev = row.checksum
        return True

    # --- risk state (key/value, survives restarts) ---
    def get_state(self, key: str) -> Any:
        with self.engine.connect() as conn:
            value = conn.execute(select(risk_state.c.value).where(risk_state.c.key == key)).scalar()
        return None if value is None else json.loads(value)

    def set_state(self, key: str, value: Any) -> None:
        body = json.dumps(value, default=str)
        now = datetime.now(UTC)
        with self.engine.begin() as conn:
            res = conn.execute(update(risk_state).where(risk_state.c.key == key).values(value=body, updated_at=now))
            if res.rowcount == 0:
                conn.execute(insert(risk_state).values(key=key, value=body, updated_at=now))

    # --- control events ---
    def control_event(self, type_: str, author: str, reason: str, ts: datetime | None = None) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(control_events).values(ts=ts or datetime.now(UTC), type=type_, author=author, reason=reason))

    def control_events(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return [row._asdict() for row in conn.execute(select(control_events).order_by(control_events.c.id))]
