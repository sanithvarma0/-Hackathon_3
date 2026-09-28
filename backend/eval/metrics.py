"""Per-incident metrics (BUILD_PLAN.md 11.1), scored against simulator ground truth.

Scoring happens here, outside the agent: the agent reports what it did (`final`), the simulator
knows what was true (incident class, true effect of each action). Used for live incidents
(Learning tab) and, in Phase 5, by the eval harness.
"""

import sqlite3
import time
from typing import Any

from backend.schemas import ActionRecord, Incident
from backend.simulator.incidents import CORRECT_FIX
from backend.usage import Totals

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_metrics (
    incident_id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    true_type TEXT NOT NULL,
    diagnosis TEXT,
    machine_id TEXT NOT NULL,
    memory_enabled INTEGER NOT NULL,
    exposure INTEGER NOT NULL,              -- k-th time this class was seen in this history
    status TEXT NOT NULL,                   -- resolved | escalated
    mttr_sim_s INTEGER,
    human_wait_sim_s INTEGER,
    agent_time_real_s REAL,
    tool_calls INTEGER,
    first_attempt_tool_calls INTEGER,
    attempts INTEGER,
    investigation_efficiency REAL,
    llm_confidence REAL,
    calibrated_confidence REAL,
    memory_hit INTEGER,
    top_match_same_class INTEGER,           -- NULL when memory returned no match
    recommendation_correct INTEGER,         -- first recommendation == the class's correct fix
    first_time_right INTEGER,               -- first executed action fully resolved it
    false_replay INTEGER,                   -- first recommendation = another class's fix
    escalated INTEGER,
    llm_tokens INTEGER,
    cost_usd REAL
);
"""

CLASS_FIXES = {fix for fix in CORRECT_FIX.values() if fix != "ESCALATE_HUMAN"}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def build_row(
    *,
    final: dict[str, Any],
    incident: Incident,
    actions: list[ActionRecord],
    memory_enabled: bool,
    agent_time_real_s: float,
    usage: Totals | None,
    match_type: str | None,
    exposure: int,
) -> dict[str, Any]:
    correct = CORRECT_FIX[incident.type]
    first = final.get("first_action_recommended")
    return {
        "incident_id": incident.id,
        "ts": time.time(),
        "true_type": incident.type,
        "diagnosis": final.get("diagnosis"),
        "machine_id": incident.machine_id,
        "memory_enabled": int(memory_enabled),
        "exposure": exposure,
        "status": final.get("status", incident.status),
        "mttr_sim_s": incident.mttr_sim_s,
        "human_wait_sim_s": incident.human_wait_sim_s,
        "agent_time_real_s": agent_time_real_s,
        "tool_calls": final.get("tool_calls"),
        "first_attempt_tool_calls": final.get("first_attempt_tool_calls"),
        "attempts": final.get("attempts"),
        "investigation_efficiency": final.get("investigation_efficiency"),
        "llm_confidence": final.get("first_confidence"),
        "calibrated_confidence": final.get("first_calibrated_confidence"),
        "memory_hit": int(bool(final.get("memory_hit"))),
        "top_match_same_class": None if match_type is None else int(match_type == incident.type),
        "recommendation_correct": int(first == correct),
        "first_time_right": int(bool(actions) and actions[0].effect == "full_recovery"),
        "false_replay": int(first in CLASS_FIXES and first != correct),
        "escalated": int(incident.status == "escalated"),
        "llm_tokens": usage.total_tokens if usage else None,
        "cost_usd": round(usage.cost_usd, 6) if usage else None,
    }


def record(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    conn.execute(
        f"INSERT OR REPLACE INTO live_metrics ({cols}) VALUES ({marks})", tuple(row.values())
    )  # noqa: S608,E501
    conn.commit()


def exposure_of(conn: sqlite3.Connection, true_type: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM live_metrics WHERE true_type = ?", (true_type,)
    ).fetchone()
    return int(row[0]) + 1


def type_of(conn: sqlite3.Connection, incident_id: str) -> str | None:
    row = conn.execute(
        "SELECT true_type FROM live_metrics WHERE incident_id = ?", (incident_id,)
    ).fetchone()
    return None if row is None else str(row[0])


def series(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute("SELECT * FROM live_metrics ORDER BY ts")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def clear(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM live_metrics")
    conn.commit()
