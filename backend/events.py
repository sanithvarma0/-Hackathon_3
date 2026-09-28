"""Event bus behind the single global SSE stream (BUILD_PLAN.md 9).

- Every event gets a monotonically increasing ID; IDs continue across restarts (persisted).
- A ring buffer serves `Last-Event-ID` replay, so a reconnecting browser misses nothing.
- Every event except the 1 Hz `state_changed` tick is also persisted, so an incident's full
  trace can be shown after the fact (Memory Browser, incident detail).
- Each subscriber has a bounded queue; a stalled client loses its oldest events rather than
  slowing everyone else down.
"""

import asyncio
import json
import sqlite3
import time
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

SCHEMA = """
CREATE TABLE IF NOT EXISTS bus_events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    incident_id TEXT,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bus_events_incident ON bus_events (incident_id, id);
"""

EPHEMERAL = {"state_changed"}  # high-frequency ticks: streamed, never persisted


class BusEvent(BaseModel):
    id: int
    ts: float
    type: str
    incident_id: str | None
    data: dict[str, Any]


class EventBus:
    def __init__(
        self, conn: sqlite3.Connection | None = None, *, buffer: int = 2000, queue: int = 1000
    ) -> None:
        self._conn = conn
        self._buffer: deque[BusEvent] = deque(maxlen=buffer)
        self._queue_size = queue
        self._subscribers: set[asyncio.Queue[BusEvent]] = set()
        self._next_id = 1
        if conn is not None:
            conn.executescript(SCHEMA)
            row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM bus_events").fetchone()
            self._next_id = int(row[0]) + 1

    def publish(self, type: str, incident_id: str | None, data: dict[str, Any]) -> BusEvent:
        event = BusEvent(
            id=self._next_id, ts=time.time(), type=type, incident_id=incident_id, data=data
        )
        self._next_id += 1
        self._buffer.append(event)
        if self._conn is not None and type not in EPHEMERAL:
            self._conn.execute(
                "INSERT INTO bus_events (id, ts, type, incident_id, data) VALUES (?, ?, ?, ?, ?)",
                (event.id, event.ts, type, incident_id, json.dumps(data, default=str)),
            )
            self._conn.commit()
        for q in list(self._subscribers):
            if q.full():
                q.get_nowait()  # drop this slow subscriber's oldest event
            q.put_nowait(event)
        return event

    def since(self, last_id: int) -> list[BusEvent]:
        return [e for e in self._buffer if e.id > last_id]

    async def subscribe(self, last_id: int | None = None) -> AsyncIterator[BusEvent]:
        q: asyncio.Queue[BusEvent] = asyncio.Queue(self._queue_size)
        self._subscribers.add(q)
        try:
            replayed = 0
            if last_id is not None:
                for event in self.since(last_id):
                    replayed = event.id
                    yield event
            while True:
                event = await q.get()
                if event.id > replayed:  # skip anything already sent during replay
                    yield event
        finally:
            self._subscribers.discard(q)

    def incident_events(self, incident_id: str) -> list[BusEvent]:
        if self._conn is None:
            return [e for e in self._buffer if e.incident_id == incident_id]
        rows = self._conn.execute(
            "SELECT id, ts, type, incident_id, data FROM bus_events WHERE incident_id = ? "
            "ORDER BY id",
            (incident_id,),
        ).fetchall()
        return [
            BusEvent(id=r[0], ts=r[1], type=r[2], incident_id=r[3], data=json.loads(r[4]))
            for r in rows
        ]

    def clear(self) -> None:
        self._buffer.clear()
        if self._conn is not None:
            self._conn.execute("DELETE FROM bus_events")
            self._conn.commit()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
