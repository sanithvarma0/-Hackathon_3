"""The two deterministic memory touchpoints (BUILD_PLAN.md 6.3). No LLM decides whether to
use memory; both are skipped entirely when memory is OFF."""

from typing import Any

from backend.agent.deps import AgentDeps
from backend.agent.models import InvestigationSummary
from backend.agent.state import AgentState
from backend.memory.render import evidence_query, hints_query
from backend.schemas import Alert

MAX_HINTS = 6


def _memory_off(state: AgentState, deps: AgentDeps, node: str) -> bool:
    if state["memory_enabled"] and deps.memory is not None:
        return False
    deps.emit("memory_skipped", state["incident_id"], {"node": node})
    return True


async def recall_hints(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    """Touchpoint #1, before investigating: what did similar past incidents hinge on?"""
    incident_id = state["incident_id"]
    if _memory_off(state, deps, "recall_hints"):
        return {"hints": [], "runbook": None}
    assert deps.memory is not None
    query = hints_query(Alert.model_validate(state["alert"]))
    with deps.tracer.span("recall_hints", as_type="retriever", input=query) as span:
        try:
            recall = await deps.memory.recall(query, exclude_incident=incident_id)
            runbook = await deps.memory.runbook()
        except Exception as e:
            deps.emit("error", incident_id, {"code": "MEMORY_UNAVAILABLE", "message": str(e)})
            span.update(output={"error": str(e)}, level="WARNING")
            return {"hints": [], "runbook": None}
        hints = [f for m in recall.matches for f in m.facts][:MAX_HINTS]
        hints += [p for p in recall.learned_patterns[:2] if p not in hints]
        if not hints:
            runbook = None  # an empty bank's runbook says nothing useful; don't inject noise
        span.update(output={"hints": hints, "runbook": bool(runbook)})
    deps.emit(
        "memory_hints",
        incident_id,
        {"query": query, "hints": hints, "runbook": runbook, "matches": len(recall.matches)},
    )
    return {"hints": hints, "runbook": runbook}


async def search_memory(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    """Touchpoint #2, after investigating: which past incidents share this signature?"""
    incident_id = state["incident_id"]
    first_attempt = not state.get("attempts")
    update: dict[str, Any]
    if _memory_off(state, deps, "search_memory"):
        update = {"memory_results": [], "learned_patterns": []}
        if first_attempt:
            update["memory_hit"] = False
        return update
    assert deps.memory is not None
    summary = InvestigationSummary.model_validate(state["summary"])
    query = evidence_query(summary)
    if not summary.complete or ";" not in query:
        # No observed signals to match on. Measured live: a bare "output decline" query returns
        # confident-looking false matches, which then pulled the decision the wrong way.
        deps.emit(
            "memory_skipped",
            incident_id,
            {"node": "search_memory", "reason": "not enough evidence for a memory query"},
        )
        update = {"memory_results": [], "learned_patterns": [], "memory_query": query}
        if first_attempt:
            update["memory_hit"] = False
        return update
    deps.emit("memory_search", incident_id, {"query": query})
    with deps.tracer.span("search_memory", as_type="retriever", input=query) as span:
        try:
            recall = await deps.memory.recall(query, exclude_incident=incident_id)
        except Exception as e:
            deps.emit("error", incident_id, {"code": "MEMORY_UNAVAILABLE", "message": str(e)})
            span.update(output={"error": str(e)}, level="WARNING")
            return {"memory_results": [], "learned_patterns": [], "memory_query": query}
        matches = [m.model_dump() for m in recall.matches]
        span.update(output={"matches": [(m["incident_id"], m["rerank"]) for m in matches]})
    deps.emit(
        "memory_results",
        incident_id,
        {"query": query, "matches": matches, "learned_patterns": list(recall.learned_patterns)},
    )
    update = {
        "memory_query": query,
        "memory_results": matches,
        "learned_patterns": list(recall.learned_patterns),
    }
    if first_attempt:
        update["memory_hit"] = bool(matches)
        update["first_memory_results"] = matches
    return update
