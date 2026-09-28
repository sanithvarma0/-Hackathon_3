"""Run the agent on one incident: start, pause for a human, resume (BUILD_PLAN.md 7.2, 9).

Graph state lives in the LangGraph checkpointer keyed by incident ID (thread_id), so the API
can pause at the approval step and resume from any later request. Any unexpected failure is
turned into an `error` event plus an escalation — the incident is never left half-handled.
"""

import time
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from backend.adapters import AdapterError
from backend.agent.deps import AgentDeps
from backend.agent.graph import build_graph
from backend.agent.state import AgentState
from backend.schemas import ACTIONS


@dataclass(frozen=True)
class RunResult:
    incident_id: str
    status: Literal["awaiting_action", "finished", "failed"]
    state: dict[str, Any]
    agent_time_real_s: float
    trace_url: str | None


class AgentRunner:
    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps
        self._graph = build_graph(deps)
        self._agent_time: dict[str, float] = {}

    async def start(
        self,
        incident_id: str,
        *,
        memory_enabled: bool,
        approval: Literal["auto", "human"] = "human",
        forced_actions: dict[int, str] | None = None,
    ) -> RunResult:
        alert = self._deps.lifecycle.get_alert(incident_id)
        state: AgentState = {
            "incident_id": incident_id,
            "memory_enabled": memory_enabled,
            "approval": approval,
            "forced_actions": {str(k): v for k, v in (forced_actions or {}).items()},
            "alert": alert.model_dump(),
        }
        self._deps.emit(
            "investigation_queued",
            incident_id,
            {"memory_enabled": memory_enabled, "alert": alert.model_dump()},
        )
        return await self._run(incident_id, state, memory_enabled)

    async def resume(self, incident_id: str, action: str) -> RunResult:
        if action not in ACTIONS:
            raise ValueError(f"{action!r} is not a whitelisted action")
        return await self._run(incident_id, Command(resume={"action": action}), None)

    def pending_recommendation(self, incident_id: str) -> dict[str, Any] | None:
        snapshot = self._graph.get_state(self._config(incident_id))
        for task in snapshot.tasks:
            for intr in task.interrupts:
                value: dict[str, Any] = intr.value
                return value
        return None

    async def _run(self, incident_id: str, payload: Any, memory_enabled: bool | None) -> RunResult:
        started = time.perf_counter()
        meta = {} if memory_enabled is None else {"memory_enabled": str(memory_enabled)}
        with self._deps.tracer.run(incident_id, **meta) as root:
            try:
                result = await self._graph.ainvoke(payload, self._config(incident_id))
            except Exception as e:
                self._deps.emit(
                    "error",
                    incident_id,
                    {"code": "AGENT_ERROR", "message": f"{type(e).__name__}: {e}"},
                )
                self._fail_safe(incident_id)
                root.update(output={"error": str(e)}, level="ERROR")
                return self._result(incident_id, "failed", {}, started)
            status: Literal["awaiting_action", "finished"] = (
                "awaiting_action" if result.get("__interrupt__") else "finished"
            )
            root.update(output=result.get("final") or {"status": status})
            url = self._deps.tracer.current_trace_url()
        return self._result(incident_id, status, dict(result), started, url)

    def _fail_safe(self, incident_id: str) -> None:
        """Leave the incident with a human rather than half-handled."""
        try:
            self._deps.adapter.execute_action(incident_id, "ESCALATE_HUMAN")
        except AdapterError:
            pass  # already closed

    def _result(
        self,
        incident_id: str,
        status: Literal["awaiting_action", "finished", "failed"],
        state: dict[str, Any],
        started: float,
        url: str | None = None,
    ) -> RunResult:
        spent = self._agent_time.get(incident_id, 0.0) + time.perf_counter() - started
        self._agent_time[incident_id] = spent
        return RunResult(incident_id, status, state, round(spent, 2), url)

    @staticmethod
    def _config(incident_id: str) -> RunnableConfig:
        return RunnableConfig(configurable={"thread_id": incident_id})
