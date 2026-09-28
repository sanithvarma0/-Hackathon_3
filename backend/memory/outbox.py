"""Write-ahead outbox for memory writes (BUILD_PLAN.md 6.2 "Outage handling").

Every episode/lesson is stored in SQLite first (this is also what the Memory Browser shows —
the exact text retained), then retained to Hindsight. A failed retain is marked and retried
later; the incident flow never blocks or crashes on a memory outage.
"""

import json
import sqlite3
from datetime import UTC, datetime
from typing import Literal

from backend.memory.store import MemoryRecord, MemoryStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    document_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    metadata TEXT NOT NULL,
    created_ts INTEGER NOT NULL,
    retain_status TEXT NOT NULL,        -- pending | retained | failed
    error TEXT
);
"""

RetainStatus = Literal["pending", "retained", "failed"]


class MemoryWriter:
    def __init__(self, conn: sqlite3.Connection, store: MemoryStore | None) -> None:
        self._conn = conn
        self._store = store
        conn.executescript(SCHEMA)

    async def write(self, record: MemoryRecord) -> RetainStatus:
        self._conn.execute(
            "INSERT OR REPLACE INTO episodes (document_id, incident_id, kind, text, metadata, "
            "created_ts, retain_status) VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (
                record.document_id,
                record.incident_id,
                record.kind,
                record.text,
                json.dumps(record.metadata),
                int(record.timestamp.timestamp()),
            ),
        )
        self._conn.commit()
        return await self._retain(record)

    async def _retain(self, record: MemoryRecord) -> RetainStatus:
        if self._store is None:
            self._mark(record.document_id, "failed", "memory store not configured")
            return "failed"
        try:
            await self._store.retain(record)
        except Exception as e:  # any outage: keep the record, retry later
            self._mark(record.document_id, "failed", f"{type(e).__name__}: {e}"[:500])
            return "failed"
        self._mark(record.document_id, "retained", None)
        return "retained"

    async def retry_failed(self) -> int:
        """Re-send failed records (called by a background loop). Returns how many succeeded."""
        rows = self._conn.execute(
            "SELECT * FROM episodes WHERE retain_status = 'failed' ORDER BY created_ts"
        ).fetchall()
        done = 0
        for r in rows:
            record = MemoryRecord(
                document_id=r["document_id"],
                incident_id=r["incident_id"],
                kind=r["kind"],
                text=r["text"],
                metadata=json.loads(r["metadata"]),
                timestamp=datetime.fromtimestamp(r["created_ts"], UTC),
            )
            if await self._retain(record) == "retained":
                done += 1
        return done

    def records(self, incident_id: str | None = None) -> list[sqlite3.Row]:
        if incident_id is None:
            return self._conn.execute("SELECT * FROM episodes ORDER BY created_ts").fetchall()
        return self._conn.execute(
            "SELECT * FROM episodes WHERE incident_id = ? ORDER BY created_ts", (incident_id,)
        ).fetchall()

    def _mark(self, document_id: str, status: RetainStatus, error: str | None) -> None:
        self._conn.execute(
            "UPDATE episodes SET retain_status = ?, error = ? WHERE document_id = ?",
            (status, error, document_id),
        )
        self._conn.commit()
