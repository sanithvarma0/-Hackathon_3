"""Headless evaluation runs (BUILD_PLAN.md 11): the same agent as the demo, on the simulator,
with auto-approval and a fast-forward clock.

A *unit* is one (condition, seed): a fresh simulator world, a fresh throwaway memory bank and
one incident sequence (`sequence.py`). Units are independent, so they run in parallel and an
interrupted eval resumes unit by unit (a half-finished unit is re-run from scratch: its world
and its memory are inseparable from its history).

Fast-forward cost model — the simulated clock only moves when something advances it, so agent
work is charged explicitly and deterministically (5.1):
  - every tool call costs TOOL_CALL_SIM_S of plant time (measured, see below);
  - verify windows, detection and escalation penalties cost what the simulator defines;
  - the human approval step costs 0 (auto-approval);
so MTTR is driven by what the agent did (tool calls, attempts, wrong fixes), never by API
latency. TOOL_CALL_SIM_S: the live UI session (14.1e) measured 3.5 s of real agent time per
tool call (LLM + tools + memory, 8 incidents); at the demo's SIM_SPEED of 10 that is 35 s.
"""

import asyncio
import contextlib
import json
import random
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from backend.agent.deps import Emit, WaitSim
from backend.eval import metrics
from backend.eval.sequence import Planned, build_sequence, family
from backend.service import AgentHandle
from backend.simulator import ManualClock, Simulator
from backend.simulator.db import connect
from backend.usage import Totals, current_incident

TOOL_CALL_SIM_S = 35
DETECT_STEP_SIM_S = 10
EVAL_EPOCH = 1_788_220_800.0  # 2026-09-01 00:00 UTC: every unit's plant starts here

Condition = Literal["memory_on", "memory_off"]
CONDITIONS: tuple[Condition, ...] = ("memory_on", "memory_off")

EvalAgentFactory = Callable[
    [Simulator, sqlite3.Connection, str, Emit, WaitSim, str], Awaitable[AgentHandle]
]
RowSink = Callable[[dict[str, Any]], None]


class EvalBudgetExceeded(Exception):
    """This eval's spend (LLM + Hindsight) reached --max-usd; stop cleanly."""


@dataclass(frozen=True)
class Unit:
    run_id: str
    condition: Condition
    seed: int
    n_incidents: int
    battery: str = "textbook"

    @property
    def key(self) -> str:
        return f"{self.condition}-s{self.seed}"

    @property
    def run_label(self) -> str:
        return f"eval-{self.run_id}-{self.key}"

    @property
    def bank_id(self) -> str:
        return f"memoryops-{self.run_label}"


@dataclass
class _Probe:
    """Per-incident bookkeeping filled from the agent's own events."""

    tool_calls: int = 0
    events: list[str] = field(default_factory=list)


def plan_units(
    run_id: str, seeds: list[int], n_incidents: int, battery: str = "textbook"
) -> list[Unit]:
    # Seeds outer, conditions inner: with limited parallelism, pairs finish together.
    return [Unit(run_id, c, s, n_incidents, battery) for s in seeds for c in CONDITIONS]


async def run_unit(
    unit: Unit,
    factory: EvalAgentFactory,
    *,
    sink: RowSink,
    escalation_penalty_sim_s: int = 1800,
    budget: Callable[[], None] | None = None,
    keep_bank: bool = False,
    settle_s: float = 30,
) -> list[dict[str, Any]]:
    """Run one unit end to end; every finished incident goes to `sink` immediately."""
    conn = connect(":memory:")
    clock = ManualClock(EVAL_EPOCH + unit.seed * 86_400)
    sim = Simulator(conn, clock, seed=unit.seed, escalation_penalty_sim_s=escalation_penalty_sim_s)
    probe = _Probe()

    def emit(kind: str, incident_id: str, data: dict[str, Any]) -> None:
        probe.events.append(kind)
        if kind == "tool_call":  # the cost model: plant time passes while the agent works
            probe.tool_calls += 1
            clock.advance(TOOL_CALL_SIM_S)
            sim.tick()

    async def wait_sim(seconds: int) -> None:
        clock.advance(seconds)
        sim.tick()

    agent = await factory(sim, conn, unit.bank_id, emit, wait_sim, unit.run_label)
    rows: list[dict[str, Any]] = []
    try:
        for planned in build_sequence(unit.seed, unit.n_incidents, unit.battery):
            if budget is not None:
                budget()
            row = await _run_incident(unit, planned, sim, clock, agent, probe)
            rows.append(row)
            sink(row)
        await _settle_refreshes(agent, rows[-1]["incident_id"] if rows else None, settle_s)
    finally:
        with contextlib.suppress(Exception):
            if not keep_bank and agent.delete_bank is not None:
                await _retry(agent.delete_bank)
        await agent.close()
    return rows


async def _run_incident(
    unit: Unit,
    planned: Planned,
    sim: Simulator,
    clock: ManualClock,
    agent: AgentHandle,
    probe: _Probe,
) -> dict[str, Any]:
    clock.advance(planned.gap_sim_s)
    sim.tick()
    incident = sim.trigger_incident(planned.type, planned.machine, spec_seed=planned.spec_seed)
    while sim.get_incident(incident.id).detected_ts is None:
        clock.advance(DETECT_STEP_SIM_S)
        sim.tick()

    probe.tool_calls, probe.events = 0, []
    memory_on = unit.condition == "memory_on"
    history = [sim.get_incident(i.id) for i in sim.list_incidents() if i.id != incident.id]
    started = time.perf_counter()
    result = await agent.runner.start(incident.id, memory_enabled=memory_on, approval="auto")
    real_s = time.perf_counter() - started
    error = None
    if sim.active_incident() is not None:  # never let a failed run block the next incident
        error = f"run ended with status {result.status}; escalated by the harness"
        sim.execute_action(incident.id, "ESCALATE_HUMAN")

    token = current_incident.set(incident.id)  # a refresh triggered by this episode is its cost
    try:
        if agent.memory is not None:
            await agent.memory.check_refresh()
    finally:
        current_incident.reset(token)

    final: dict[str, Any] = result.state.get("final", {})
    closed = sim.get_incident(incident.id)
    types = {i.id: i.type for i in history}
    matches = [
        {
            "incident_id": m["incident_id"],
            "type": types.get(m["incident_id"]),
            "rank": m["rank"],
            "rerank": m["rerank"],
            "strength": m["strength"],
        }
        for m in final.get("first_memory_results") or []
    ]
    usage: Totals | None = (
        agent.ledger.totals(incident_id=incident.id, run_label=unit.run_label)
        if agent.ledger is not None
        else None
    )
    row = metrics.build_row(
        final=final,
        incident=closed,
        actions=sim.action_log(incident.id),
        memory_enabled=memory_on,
        agent_time_real_s=round(real_s, 2),
        usage=usage,
        match_type=matches[0]["type"] if matches else None,
        exposure=planned.exposure,
    )
    row.update(
        run_id=unit.run_id,
        battery=unit.battery,
        family=family(planned.type),
        condition=unit.condition,
        seed=unit.seed,
        position=planned.position,
        held_out=planned.held_out,
        after_config_regression=planned.after_config_regression,
        prior_same_class=planned.type in types.values(),
        prior_other_class=any(t != planned.type for t in types.values()),
        matches=matches,
        first_recommendation=final.get("first_action_recommended"),
        memory_tool_calls=final.get("memory_tool_calls", 0),
        memory_gate=final.get("memory_gate"),
        engineer_action=closed.engineer_action,
        cited=final.get("first_cited_incidents") or [],
        trace_url=result.trace_url,
        error=error,
    )
    return row


async def _settle_refreshes(agent: AgentHandle, last_incident: str | None, wait_s: float) -> None:
    """The last episode's runbook refresh happens after the run; wait briefly to count it."""
    if agent.memory is None or last_incident is None:
        return
    token = current_incident.set(last_incident)
    try:
        for _ in range(max(1, int(wait_s // 5))):
            if await agent.memory.check_refresh() or wait_s <= 0:
                return
            await asyncio.sleep(5)
    finally:
        current_incident.reset(token)


async def _retry(op: Callable[[], Awaitable[None]], attempts: int = 3) -> None:
    for attempt in range(attempts):
        try:
            await op()
            return
        except Exception:
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(2 * (attempt + 1) + random.random())


async def run_units(
    units: list[Unit],
    factory: EvalAgentFactory,
    *,
    sink: RowSink,
    parallel: int,
    on_unit_done: Callable[[Unit], None],
    escalation_penalty_sim_s: int = 1800,
    budget: Callable[[], None] | None = None,
    keep_banks: bool = False,
    settle_s: float = 30,
) -> list[tuple[Unit, BaseException]]:
    """Run units with bounded parallelism; returns the units that failed (and why)."""
    gate = asyncio.Semaphore(parallel)
    failures: list[tuple[Unit, BaseException]] = []

    async def one(unit: Unit) -> None:
        async with gate:
            try:
                await run_unit(
                    unit,
                    factory,
                    sink=sink,
                    escalation_penalty_sim_s=escalation_penalty_sim_s,
                    budget=budget,
                    keep_bank=keep_banks,
                    settle_s=settle_s,
                )
                on_unit_done(unit)
            except Exception as e:  # recorded and reported; other units carry on
                failures.append((unit, e))

    await asyncio.gather(*(one(u) for u in units))
    return failures


class Checkpoint:
    """Rows are appended as each incident finishes; a unit counts only once it is complete.

    Resuming keeps the rows of completed units and re-runs every other unit from scratch.
    """

    def __init__(self, out_dir: Path) -> None:
        self.dir = out_dir
        self.rows_path = out_dir / "rows.jsonl"
        self.units_path = out_dir / "units_done.txt"
        out_dir.mkdir(parents=True, exist_ok=True)

    def append(self, row: dict[str, Any]) -> None:
        with self.rows_path.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def unit_done(self, unit: Unit) -> None:
        with self.units_path.open("a") as f:
            f.write(unit.key + "\n")

    def completed(self) -> set[str]:
        if not self.units_path.exists():
            return set()
        return {line.strip() for line in self.units_path.read_text().splitlines() if line.strip()}

    def rows(self) -> list[dict[str, Any]]:
        """Rows of completed units only (a partial unit's rows are discarded)."""
        if not self.rows_path.exists():
            return []
        done = self.completed()
        rows = [json.loads(line) for line in self.rows_path.read_text().splitlines() if line]
        return [r for r in rows if f"{r['condition']}-s{r['seed']}" in done]

    def drop_partial(self) -> None:
        """Rewrite rows.jsonl without rows of unfinished units (before re-running them)."""
        kept = self.rows()
        self.rows_path.write_text("".join(json.dumps(r, default=str) + "\n" for r in kept))
