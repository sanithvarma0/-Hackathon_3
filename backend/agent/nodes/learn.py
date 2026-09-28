"""record_lesson and learn: every outcome becomes memory (BUILD_PLAN.md 6.2)."""

from datetime import UTC, datetime
from typing import Any

from backend.agent.deps import AgentDeps
from backend.agent.models import InvestigationSummary
from backend.agent.state import AgentState
from backend.memory.render import (
    AttemptRecord,
    EpisodeInput,
    render_episode,
    render_lesson,
)
from backend.memory.store import MemoryRecord
from backend.schemas import Alert


def _now_ts(deps: AgentDeps, machine_id: str) -> int:
    return deps.adapter.get_machine_metrics(machine_id).ts


async def record_lesson(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    """A fix just failed: retain the lesson now, before anything else can go wrong."""
    incident_id = state["incident_id"]
    alert = Alert.model_validate(state["alert"])
    attempt_no = state["attempt_count"]
    attempt = AttemptRecord(**state["outcome"])
    summary = InvestigationSummary.model_validate(state["summary"])
    text = render_lesson(
        incident_id,
        attempt_no,
        alert.machine_id,
        state["recommendation"]["signature"],
        attempt,
        summary.decisive_evidence,
    )
    record = MemoryRecord(
        document_id=f"{incident_id}:lesson-{attempt_no}",
        incident_id=incident_id,
        kind="lesson",
        text=text,
        metadata={
            "incident_id": incident_id,
            "record_kind": "lesson",
            "failed_action": attempt.action,
            "effect": attempt.effect,
            "machine_id": alert.machine_id,
        },
        timestamp=datetime.fromtimestamp(_now_ts(deps, alert.machine_id), UTC),
    )
    with deps.tracer.span("retain lesson", as_type="span", input=text):
        status = await deps.writer.write(record)
    if status != "retained":
        deps.emit(
            "error",
            incident_id,
            {"code": "MEMORY_WRITE_FAILED", "message": f"{record.document_id} queued for retry"},
        )
    deps.emit(
        "lesson_written",
        incident_id,
        {"document_id": record.document_id, "text": text, "status": status},
    )
    return {}


async def learn(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    """Close the incident, retain the episode, and report the run's facts."""
    incident_id = state["incident_id"]
    alert = Alert.model_validate(state["alert"])
    resolution = deps.lifecycle.resolve(incident_id)
    attempts = [AttemptRecord(**a) for a in state.get("attempts", [])]
    if state.get("chosen_action") == "ESCALATE_HUMAN":
        attempts.append(AttemptRecord(action="ESCALATE_HUMAN", effect="escalated"))
    rec = state["recommendation"]
    summary = InvestigationSummary.model_validate(state["summary"])
    last_steps = state.get("steps", [])
    episode = EpisodeInput(
        incident_id=incident_id,
        ts=_now_ts(deps, alert.machine_id),
        machine_id=alert.machine_id,
        machine_profile=f"{alert.machine_name}, {alert.machine_profile}",
        signature=rec["signature"],
        alert=alert,
        summary=summary,
        diagnosis=rec["diagnosis"],
        confidence=rec["confidence"],
        tool_path=[s["call"] for s in last_steps],
        tool_calls=len(state.get("all_steps", [])),
        attempts=attempts,
        outcome="escalated" if resolution.status == "escalated" else "successful",
        mttr_sim_s=resolution.mttr_sim_s,
        cited_incidents=rec.get("cited_incidents", []),
        engineer_action=resolution.engineer_action,
        engineer_note=resolution.engineer_note,
    )
    text = render_episode(episode)
    # What actually fixed it: the engineer's action after an escalation, else the agent's last.
    final_action = resolution.engineer_action or (
        attempts[-1].action if attempts else "ESCALATE_HUMAN"
    )
    record = MemoryRecord(
        document_id=incident_id,
        incident_id=incident_id,
        kind="episode",
        text=text,
        metadata={
            "incident_id": incident_id,
            "record_kind": "episode",
            "machine_id": alert.machine_id,
            "diagnosis": rec["diagnosis"],
            "final_action": final_action,
            "outcome": episode.outcome,
        },
        timestamp=datetime.fromtimestamp(episode.ts, UTC),
    )
    with deps.tracer.span("retain episode", as_type="span", input=text):
        status = await deps.writer.write(record)
    if status != "retained":
        deps.emit(
            "error",
            incident_id,
            {"code": "MEMORY_WRITE_FAILED", "message": f"{incident_id} queued for retry"},
        )
    deps.emit(
        "outcome",
        incident_id,
        {
            "resolved": resolution.status == "resolved",
            "escalated": resolution.status == "escalated",
            "mttr_sim_s": resolution.mttr_sim_s,
        },
    )
    deps.emit(
        "memory_written", incident_id, {"document_id": incident_id, "text": text, "status": status}
    )
    first = state.get("first_recommendation", rec)
    decisive = len(summary.decisive_steps)
    total_calls = len(state.get("all_steps", []))
    return {
        "final": {
            "incident_id": incident_id,
            "status": resolution.status,
            "mttr_sim_s": resolution.mttr_sim_s,
            "human_wait_sim_s": resolution.human_wait_sim_s,
            "final_action": final_action,
            "attempts": len(state.get("attempts", [])),
            "tool_calls": total_calls,
            "first_attempt_tool_calls": sum(
                1 for s in state.get("all_steps", []) if s["attempt"] == 1
            ),
            "investigation_efficiency": round(decisive / len(last_steps), 3) if last_steps else 0.0,
            "first_action_recommended": first["action"],
            "diagnosis": first.get("diagnosis"),
            "first_confidence": first["confidence"],
            "first_calibrated_confidence": first["calibrated_confidence"],
            "first_cited_incidents": first.get("cited_incidents", []),
            "memory_hit": state.get("memory_hit", False),
            "first_memory_results": state.get("first_memory_results", []),
            "episode_status": status,
        }
    }
