"""Application service: the running system behind the API (BUILD_PLAN.md 9).

Owns the simulator, the agent, the event bus and the background loops, and turns simulator
notifications into agent runs: when monitoring detects an incident, the agent starts on its own
— the same way a real alerting pipeline pages an on-call responder.

Two clock modes: realtime (the live demo; background loops tick the world at SIM_SPEED) and
manual (tests; `advance()` moves time explicitly, no background loops).
"""

import asyncio
import contextlib
import sqlite3
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from backend.agent.deps import Emit, WaitSim
from backend.agent.runner import AgentRunner, RunResult
from backend.config import Settings
from backend.eval import metrics
from backend.events import EventBus
from backend.memory.outbox import MemoryWriter
from backend.memory.store import MemoryStore
from backend.schemas import (
    Action,
    CustomIncidentSpec,
    Incident,
    IncidentType,
    Metric,
)
from backend.simulator import ManualClock, SimNotification, Simulator, SimulatorError
from backend.simulator.clock import Clock
from backend.usage import UsageLedger

AgentStatus = Literal[
    "waiting_detection", "investigating", "awaiting_action", "acting", "finished", "failed"
]
TICK_S = 1.0
OUTBOX_RETRY_S = 30.0
RUNBOOK_POLL_S = 20.0


def sim_resume_ts(conn: sqlite3.Connection) -> float | None:
    """Latest sim time memory has recorded. The simulated clock runs ahead of the wall clock
    (SIM_SPEED), so a restarted backend resumes from here: sim time must never go backwards,
    or new episodes would predate old ones."""
    try:
        row = conn.execute("SELECT MAX(created_ts) FROM episodes").fetchone()
    except sqlite3.OperationalError:  # fresh database: no episodes table yet
        return None
    return None if row is None or row[0] is None else float(row[0])


def _new_run_label() -> str:
    return f"live-{int(time.time() * 1000)}"


class ServiceError(Exception):
    """A request the system refuses, with an HTTP status and a stable code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class AgentHandle:
    """A running agent bound to one memory bank."""

    runner: AgentRunner
    writer: MemoryWriter
    memory: MemoryStore | None
    ledger: UsageLedger | None
    bank_id: str
    close: Callable[[], Awaitable[None]]
    delete_bank: Callable[[], Awaitable[None]] | None = None


AgentFactory = Callable[[str, Emit, WaitSim, str], Awaitable[AgentHandle]]


class IncidentView(BaseModel):
    """What the UI may show. The true class stays hidden until the incident closes, unless
    the judge picked it (a predefined trigger) — no spoilers before the agent diagnoses."""

    id: str
    machine_id: str
    affected: tuple[str, ...]
    gateway: str | None
    status: str
    onset_ts: int
    detected_ts: int | None
    resolved_ts: int | None
    mttr_sim_s: int | None
    human_wait_sim_s: int
    resolution_action: str | None
    engineer_note: str | None
    type: IncidentType | None
    source: str
    memory_enabled: bool
    agent_status: AgentStatus


class MemoryOpsService:
    def __init__(
        self,
        settings: Settings,
        *,
        sim: Simulator,
        conn: sqlite3.Connection,
        clock: Clock,
        agent_factory: AgentFactory,
    ) -> None:
        self.settings = settings
        self.sim = sim
        self.conn = conn
        self.clock = clock
        self.realtime = not isinstance(clock, ManualClock)
        self.bus = EventBus(conn)
        self.run_label = _new_run_label()
        self.bank_id = settings.hindsight_bank_live
        self._factory = agent_factory
        self._agent: AgentHandle | None = None
        self._meta: dict[str, dict[str, Any]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._loops: list[asyncio.Task[None]] = []
        self._last_runbook: str | None = None
        metrics.ensure_schema(conn)
        sim.subscribe(self._on_sim)

    # ---- lifecycle ------------------------------------------------------------------------

    async def start(self) -> None:
        self._agent = await self._factory(self.bank_id, self._emit, self._wait_sim, self.run_label)
        self.sim.set_incident_offset(self._last_incident_number())
        if self.realtime:
            self._loops = [
                asyncio.create_task(self._loop(TICK_S, self._tick_and_push)),
                asyncio.create_task(self._loop(OUTBOX_RETRY_S, self._retry_outbox)),
                asyncio.create_task(self._loop(RUNBOOK_POLL_S, self._watch_runbook)),
            ]

    async def stop(self) -> None:
        for task in [*self._loops, *self._tasks]:
            task.cancel()
        for task in [*self._loops, *self._tasks]:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._loops, self._tasks = [], set()
        if self._agent is not None:
            await self._agent.close()

    @property
    def agent(self) -> AgentHandle:
        if self._agent is None:
            raise ServiceError(503, "NOT_READY", "the agent is still starting")
        return self._agent

    # ---- incidents ------------------------------------------------------------------------

    def trigger(
        self,
        *,
        source: Literal["predefined", "custom", "random"],
        memory_enabled: bool,
        incident_type: IncidentType | None = None,
        machine: str | None = None,
        custom: CustomIncidentSpec | None = None,
    ) -> IncidentView:
        try:
            incident = self.sim.trigger_incident(incident_type, machine, custom)
        except SimulatorError as e:
            raise self._reject(e) from e
        self._meta[incident.id] = {
            "memory_enabled": memory_enabled,
            "source": source,
            "agent_status": "waiting_detection",
            "reveal": source == "predefined",
        }
        view = self.view(incident)
        self.bus.publish("incident_triggered", incident.id, {"incident": view.model_dump()})
        return view

    async def act(self, incident_id: str, action: Action) -> None:
        meta = self._require_meta(incident_id)
        if meta["agent_status"] != "awaiting_action":
            raise ServiceError(
                409,
                "NOT_AWAITING_ACTION",
                f"{incident_id} is not waiting for a decision (status: {meta['agent_status']})",
            )
        meta["agent_status"] = "acting"
        self._spawn(self._run(incident_id, self.agent.runner.resume(incident_id, action)))

    def ignore(self, incident_id: str) -> None:
        self._require_meta(incident_id)
        try:
            self.sim.ignore(incident_id)
        except SimulatorError as e:
            raise self._reject(e) from e
        self.bus.publish(
            "incident_ignored",
            incident_id,
            {"message": "alarm acknowledged without action; the condition is worsening"},
        )

    def view(self, incident: Incident) -> IncidentView:
        meta = self._meta.get(incident.id, {})
        closed = incident.status in ("resolved", "escalated")
        return IncidentView(
            id=incident.id,
            machine_id=incident.machine_id,
            affected=incident.affected,
            gateway=incident.gateway,
            status=incident.status,
            onset_ts=incident.onset_ts,
            detected_ts=incident.detected_ts,
            resolved_ts=incident.resolved_ts,
            mttr_sim_s=incident.mttr_sim_s,
            human_wait_sim_s=incident.human_wait_sim_s,
            resolution_action=incident.resolution_action,
            engineer_note=incident.engineer_note,
            type=incident.type if (closed or meta.get("reveal")) else None,
            source=meta.get("source", "unknown"),
            memory_enabled=meta.get("memory_enabled", True),
            agent_status=meta.get("agent_status", "finished" if closed else "waiting_detection"),
        )

    def incident(self, incident_id: str) -> dict[str, Any]:
        try:
            incident = self.sim.get_incident(incident_id)
        except SimulatorError as e:
            raise self._reject(e) from e
        return {
            "incident": self.view(incident).model_dump(),
            "pending": self.agent.runner.pending_recommendation(incident_id),
            "events": [e.model_dump() for e in self.bus.incident_events(incident_id)],
            "memory_records": [dict(r) for r in self.agent.writer.records(incident_id)],
            "metrics": next(
                (m for m in metrics.series(self.conn) if m["incident_id"] == incident_id), None
            ),
        }

    def incidents(self) -> list[dict[str, Any]]:
        return [self.view(i).model_dump() for i in self.sim.list_incidents()]

    # ---- reads ----------------------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        plant = self.sim.plant_state()
        active = self.sim.active_incident()
        return {
            "plant": plant.model_dump(),
            "active_incident": self.view(active).model_dump() if active else None,
            "bank_id": self.bank_id,
            "sim_speed": self.settings.sim_speed if self.realtime else None,
            "last_event_id": self.bus.last_id,  # open the stream from here: nothing missed
        }

    def memory_records(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.agent.writer.records()]

    async def runbook(self) -> dict[str, Any]:
        memory = self.agent.memory
        content = await memory.runbook() if memory is not None else None
        return {"bank_id": self.bank_id, "content": content}

    def usage(self) -> dict[str, Any]:
        ledger = self.agent.ledger
        if ledger is None:
            return {"available": False}
        all_time = ledger.totals()
        session = ledger.totals(run_label=ledger.run_label)
        by_model = ledger.rows(
            "SELECT provider, model, COUNT(*), SUM(prompt_tokens + completion_tokens), "
            "COALESCE(SUM(cost_usd), 0) FROM llm_usage GROUP BY provider, model"
        )
        return {
            "available": True,
            "cap_usd": ledger.spend_cap_usd,
            "all_time": {**all_time.__dict__, "total_tokens": all_time.total_tokens},
            "session": {**session.__dict__, "total_tokens": session.total_tokens},
            "by_model": [
                {"provider": p, "model": m, "calls": c, "tokens": t, "cost_usd": cost}
                for p, m, c, t, cost in by_model
            ],
        }

    def plant_history(self, metric: Metric, minutes: float) -> dict[str, list[dict[str, float]]]:
        """Recent sim history per machine (10 s grid), so sparklines are full on page load."""
        if not 1 <= minutes <= 180:
            raise ServiceError(422, "INVALID_WINDOW", "minutes must be between 1 and 180")
        return {
            m.machine_id: [
                {"ts": p.ts, "value": p.value}
                for p in self.sim.get_metric_history(m.machine_id, metric, minutes / 60)
            ]
            for m in self.sim.plant_state().machines
        }

    def metric_series(self) -> list[dict[str, Any]]:
        return metrics.series(self.conn)

    # ---- admin ----------------------------------------------------------------------------

    async def reset(self, bank: Literal["live", "seeded"], wipe_memory: bool) -> dict[str, Any]:
        for task in list(self._tasks):
            task.cancel()
        bank_id = (
            self.settings.hindsight_bank_live
            if bank == "live"
            else self.settings.hindsight_bank_seeded
        )
        if wipe_memory and bank == "seeded":
            raise ServiceError(
                400, "SEEDED_IS_READ_ONLY", "the seeded bank is rebuilt by make seed"
            )
        if wipe_memory and self._agent is not None and self._agent.delete_bank is not None:
            await self._agent.delete_bank()
        if wipe_memory:
            self.run_label = _new_run_label()  # INC-001 again: keep its spend apart from the old
            self.conn.execute("DELETE FROM episodes")
            metrics.clear(self.conn)
            self.bus.clear()
        if bank_id != self.bank_id or wipe_memory:
            if self._agent is not None:
                await self._agent.close()
            self.bank_id = bank_id
            self._agent = await self._factory(bank_id, self._emit, self._wait_sim, self.run_label)
        self._meta.clear()
        self._last_runbook = None
        self.sim.reset()
        self.sim.set_incident_offset(self._last_incident_number())
        self.bus.publish("reset", None, {"bank_id": bank_id, "wipe_memory": wipe_memory})
        return {"bank_id": bank_id, "wipe_memory": wipe_memory}

    # ---- manual clock (tests) -------------------------------------------------------------

    async def advance(self, seconds: float) -> None:
        if not isinstance(self.clock, ManualClock):
            raise RuntimeError("advance() is only for the manual clock")
        self.clock.advance(seconds)
        self.sim.tick()
        await asyncio.sleep(0)

    async def drain(self) -> None:
        """Wait until no agent run is in flight."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ---- internals ------------------------------------------------------------------------

    def _emit(self, kind: str, incident_id: str, data: dict[str, Any]) -> None:
        meta = self._meta.get(incident_id)
        if meta is not None and kind == "investigation_start":
            meta["agent_status"] = "investigating"
        # "awaiting_action" status is set only once the graph has actually paused (_run):
        # the event is emitted a moment earlier, and a click in that gap could not resume.
        self.bus.publish(kind, incident_id, data)

    def _on_sim(self, note: SimNotification) -> None:
        self.bus.publish(note.kind, note.incident_id, {"message": note.message, "ts": note.ts})
        if note.kind == "incident_detected" and note.incident_id in self._meta:
            meta = self._meta[note.incident_id]
            if meta["agent_status"] == "waiting_detection":
                meta["agent_status"] = "investigating"
                self._spawn(
                    self._run(
                        note.incident_id,
                        self.agent.runner.start(
                            note.incident_id,
                            memory_enabled=meta["memory_enabled"],
                            approval="human",
                        ),
                    )
                )

    async def _run(self, incident_id: str, run: Coroutine[Any, Any, RunResult]) -> None:
        result = await run
        meta = self._meta.get(incident_id, {})
        if result.status == "awaiting_action":
            meta["agent_status"] = "awaiting_action"
            return
        meta["agent_status"] = "finished" if result.status == "finished" else "failed"
        meta["reveal"] = True
        if result.status == "finished":
            self._record_metrics(incident_id, result)

    def _record_metrics(self, incident_id: str, result: RunResult) -> None:
        final = result.state.get("final", {})
        incident = self.sim.get_incident(incident_id)
        matches = final.get("first_memory_results") or []
        match_type = metrics.type_of(self.conn, matches[0]["incident_id"]) if matches else None
        ledger = self.agent.ledger
        row = metrics.build_row(
            final=final,
            incident=incident,
            actions=self.sim.action_log(incident_id),
            memory_enabled=self._meta.get(incident_id, {}).get("memory_enabled", True),
            agent_time_real_s=result.agent_time_real_s,
            # scoped to this run: incident numbers restart after a wiped reset
            usage=ledger.totals(incident_id=incident_id, run_label=ledger.run_label)
            if ledger
            else None,
            match_type=match_type,
            exposure=metrics.exposure_of(self.conn, incident.type),
        )
        metrics.record(self.conn, row)
        self.bus.publish(
            "metrics_updated",
            incident_id,
            {"row": row, "incident": self.view(incident).model_dump(), "trace": result.trace_url},
        )

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _wait_sim(self, seconds: int) -> None:
        if isinstance(self.clock, ManualClock):
            self.clock.advance(seconds)
            self.sim.tick()
            return
        await asyncio.sleep(seconds / self.settings.sim_speed)

    async def _loop(self, every_s: float, step: Callable[[], Awaitable[None]]) -> None:
        while True:
            try:
                await step()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # a background hiccup must never take the service down
                self.bus.publish("error", None, {"code": "BACKGROUND_ERROR", "message": str(e)})
            await asyncio.sleep(every_s)

    async def _tick_and_push(self) -> None:
        self.bus.publish("state_changed", None, self.sim.plant_state().model_dump())

    async def _retry_outbox(self) -> None:
        if self._agent is not None and self._agent.memory is not None:
            done = await self._agent.writer.retry_failed()
            if done:
                self.bus.publish("memory_synced", None, {"retained": done})

    async def _watch_runbook(self) -> None:
        if self._agent is None or self._agent.memory is None:
            return
        content = await self._agent.memory.runbook()
        if content and content != self._last_runbook:
            self._last_runbook = content
            self.bus.publish("runbook_updated", None, {"content": content})

    def _require_meta(self, incident_id: str) -> dict[str, Any]:
        if incident_id not in self._meta:
            raise ServiceError(
                404, "INCIDENT_NOT_FOUND", f"no active-session incident {incident_id}"
            )
        return self._meta[incident_id]

    def _reject(self, e: SimulatorError) -> ServiceError:
        status = 404 if e.code in ("INCIDENT_NOT_FOUND", "UNKNOWN_MACHINE") else 409
        if e.code in ("INVALID_TYPE", "INVALID_ACTION", "INVALID_METRIC"):
            status = 422
        self.bus.publish("error", None, {"code": e.code, "message": e.message})
        return ServiceError(status, e.code, e.message)

    def _last_incident_number(self) -> int:
        """Highest incident number already in memory: IDs must never repeat across restarts."""
        numbers = [0]
        for r in self.conn.execute("SELECT DISTINCT incident_id FROM episodes"):
            digits = str(r[0]).removeprefix("INC-")
            if digits.isdigit():
                numbers.append(int(digits))
        return max(numbers)
