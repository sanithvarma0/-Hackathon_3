"""SQLite storage for simulator state (BUILD_PLAN.md 5.3).

The engine is the only writer. Timestamps are integer sim-seconds since the Unix epoch.
"""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS machines (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    profile TEXT NOT NULL,
    gateway TEXT NOT NULL,
    status TEXT NOT NULL,
    throughput_pct REAL NOT NULL,
    oee_pct REAL NOT NULL,
    error_rate_pct REAL NOT NULL,
    temperature_c REAL NOT NULL,
    sensor_variance REAL NOT NULL,
    packet_loss_pct REAL NOT NULL,
    latency_ms REAL NOT NULL,
    memory_pct REAL NOT NULL,
    config_version TEXT NOT NULL,
    last_calibration_ts INTEGER NOT NULL,
    updated_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS machine_history (
    ts INTEGER NOT NULL,
    machine_id TEXT NOT NULL,
    throughput_pct REAL NOT NULL,
    oee_pct REAL NOT NULL,
    error_rate_pct REAL NOT NULL,
    temperature_c REAL NOT NULL,
    sensor_variance REAL NOT NULL,
    packet_loss_pct REAL NOT NULL,
    latency_ms REAL NOT NULL,
    memory_pct REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_machine_ts ON machine_history (machine_id, ts);

CREATE TABLE IF NOT EXISTS events (
    ts INTEGER NOT NULL,
    machine_id TEXT NOT NULL,          -- machine ID, or gateway ID for gateway events
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_machine_ts ON events (machine_id, ts);

CREATE TABLE IF NOT EXISTS logs (
    ts INTEGER NOT NULL,
    machine_id TEXT NOT NULL,
    level TEXT NOT NULL,               -- INFO | WARN | ERROR | ALARM
    source TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_machine_ts ON logs (machine_id, ts);

CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,                -- ground truth; never exposed to the agent
    machine_id TEXT NOT NULL,
    affected TEXT NOT NULL,            -- JSON list of machine IDs
    gateway TEXT,
    signature TEXT NOT NULL,           -- JSON
    status TEXT NOT NULL,
    onset_ts INTEGER NOT NULL,
    detected_ts INTEGER,
    resolved_ts INTEGER,
    human_wait_sim_s INTEGER NOT NULL DEFAULT 0,
    resolution_action TEXT,
    mttr_sim_s INTEGER
);

CREATE TABLE IF NOT EXISTS actions_log (
    ts INTEGER NOT NULL,
    incident_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    action TEXT NOT NULL,
    effect TEXT NOT NULL,              -- ground truth: full_recovery | partial_recovery | ...
    recovery_pct REAL,
    re_degraded_after_sim_s INTEGER,
    executed_by TEXT NOT NULL
);
"""

TABLES = ("machines", "machine_history", "events", "logs", "incidents", "actions_log")


def connect(path: Path | str) -> sqlite3.Connection:
    """Open (and create if needed) the simulator database. Use ":memory:" in tests."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def clear(conn: sqlite3.Connection) -> None:
    for table in TABLES:
        conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names
    conn.commit()
