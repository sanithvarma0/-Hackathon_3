"""Everything the agent graph needs, injected once (makes every dependency fakeable in tests)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from backend.adapters import EnvironmentAdapter, IncidentLifecycle
from backend.llm import LLMClient
from backend.memory.outbox import MemoryWriter
from backend.memory.store import MemoryStore
from backend.observability import Tracer

Emit = Callable[[str, str, dict[str, Any]], None]  # (event_type, incident_id, data)
WaitSim = Callable[[int], Awaitable[None]]  # wait N sim-seconds (sleeps live, advances in eval)


@dataclass
class AgentDeps:
    adapter: EnvironmentAdapter
    lifecycle: IncidentLifecycle
    llm: LLMClient
    memory: MemoryStore | None
    writer: MemoryWriter
    tracer: Tracer
    emit: Emit
    wait_sim: WaitSim
    max_tool_calls: int = 10
    max_attempts: int = 3
    verify_window_sim_s: int = 180
