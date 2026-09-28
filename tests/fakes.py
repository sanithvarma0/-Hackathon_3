"""Fakes for the agent's external dependencies: Groq (scripted) and Hindsight (in-process)."""

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from backend.adapters import SimulatorAdapter, SimulatorLifecycle
from backend.agent.deps import AgentDeps
from backend.llm import LLMClient
from backend.memory.outbox import MemoryWriter
from backend.memory.store import MemoryMatch, MemoryRecall, MemoryRecord
from backend.observability import Tracer
from tests.conftest import World


def completion(content: str | None = None, tool_calls: list[tuple[str, dict[str, Any]]] = ()):  # type: ignore[assignment]
    calls = [
        SimpleNamespace(id=f"fake_{i}", function=SimpleNamespace(name=n, arguments=json.dumps(a)))
        for i, (n, a) in enumerate(tool_calls)
    ]
    message = SimpleNamespace(content=content, tool_calls=calls or None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)], usage=SimpleNamespace(total_tokens=100)
    )


def summary_json(**overrides: Any) -> str:
    base = {
        "onset": "gradual",
        "key_signals": ["servo timeout errors on M3", "config v9.4.0 deployed 12 min before onset"],
        "what_changed": "config deploy shortly before onset",
        "ruled_out": ["network", "sensor calibration"],
        "decisive_steps": [2],
        "decisive_evidence": "recent events showed a config deploy just before the decline",
        "summary": "Gradual decline after a config deploy.",
    }
    return json.dumps(base | overrides)


def decision_json(action: str, **overrides: Any) -> str:
    base = {
        "action": action,
        "diagnosis": "config regression",
        "signature": "gradual throughput decline shortly after a configuration deployment",
        "confidence": 0.7,
        "reasoning": "The decline began right after a config deploy.",
        "cited_incidents": [],
        "actions_known_to_fail": [],
    }
    return json.dumps(base | overrides)


class ScriptedGroq:
    """Stands in for `chat.completions.create`. Investigation: the scripted tool calls, then a
    summary. Decision: the next scripted decision (the last one repeats)."""

    def __init__(
        self,
        machine: str,
        decisions: list[str],
        *,
        tool_calls: list[tuple[str, dict[str, Any]]] | None = None,
        summary: str | None = None,
        fail: Callable[[dict[str, Any]], Exception | None] | None = None,
    ) -> None:
        self.tool_calls = (
            tool_calls
            if tool_calls is not None
            else [
                ("get_machine_metrics", {"machine_id": machine}),
                ("get_recent_events", {"machine_id": machine, "window_minutes": 60}),
                ("get_error_logs", {"machine_id": machine, "window_minutes": 30}),
            ]
        )
        self.summary = summary or summary_json()
        self.decisions = list(decisions)
        self.fail = fail
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kw: Any) -> Any:
        self.requests.append(kw)
        if self.fail is not None and (err := self.fail(kw)) is not None:
            raise err
        messages = kw["messages"]
        if messages[0]["content"].startswith("You decide"):
            text = self.decisions.pop(0) if len(self.decisions) > 1 else self.decisions[0]
            return completion(text)
        if kw.get("tools"):
            done = sum(len(m.get("tool_calls", [])) for m in messages if m["role"] == "assistant")
            if done < len(self.tool_calls):
                return completion(tool_calls=[self.tool_calls[done]])
        return completion(self.summary)

    def decide_prompts(self) -> list[str]:
        return [
            r["messages"][1]["content"]
            for r in self.requests
            if r["messages"][0]["content"].startswith("You decide")
        ]

    def investigate_prompts(self) -> list[str]:
        return [r["messages"][1]["content"] for r in self.requests if r.get("tools")]


class FakeMemory:
    """In-process MemoryStore: every stored episode matches, most recent first."""

    def __init__(self, *, fail_retain: bool = False) -> None:
        self.records: dict[str, MemoryRecord] = {}
        self.recalls: list[str] = []
        self.fail_retain = fail_retain

    async def recall(self, query: str, *, exclude_incident: str | None = None) -> MemoryRecall:
        self.recalls.append(query)
        episodes = [
            r
            for r in self.records.values()
            if r.kind == "episode" and r.incident_id != exclude_incident
        ]
        matches = tuple(
            MemoryMatch(
                incident_id=r.incident_id,
                rank=i + 1,
                rerank=round(0.9 - 0.1 * i, 2),
                similarity=0.72,
                strength="strong",
                diagnosis=r.metadata.get("diagnosis"),
                final_action=r.metadata.get("final_action"),
                outcome=r.metadata.get("outcome"),
                facts=(r.text.splitlines()[-1],),
            )
            for i, r in enumerate(reversed(episodes))
        )
        return MemoryRecall(query=query, matches=matches, learned_patterns=())

    async def runbook(self) -> str | None:
        return None

    async def retain(self, record: MemoryRecord) -> None:
        if self.fail_retain:
            raise ConnectionError("hindsight unreachable")
        self.records[record.document_id] = record


class Harness:
    """A world, an agent wired to fakes, and the events it emitted."""

    def __init__(
        self,
        world: World,
        groq: ScriptedGroq,
        memory: FakeMemory | None,
        *,
        max_attempts: int = 3,
        max_tool_calls: int = 10,
    ) -> None:
        self.world = world
        self.groq = groq
        self.memory = memory
        self.events: list[tuple[str, str, dict[str, Any]]] = []

        async def wait_sim(seconds: int) -> None:
            world.advance(seconds)

        async def no_sleep(_: float) -> None:
            return None

        self.writer = MemoryWriter(world.conn, memory)
        self.deps = AgentDeps(
            adapter=SimulatorAdapter(world.sim),
            lifecycle=SimulatorLifecycle(world.sim),
            llm=LLMClient(groq.create, ["primary", "fallback"], sleep=no_sleep),
            memory=memory,
            writer=self.writer,
            tracer=Tracer(),
            emit=lambda kind, incident_id, data: self.events.append((kind, incident_id, data)),
            wait_sim=wait_sim,
            max_tool_calls=max_tool_calls,
            max_attempts=max_attempts,
        )

    def kinds(self) -> list[str]:
        return [k for k, _, _ in self.events]
