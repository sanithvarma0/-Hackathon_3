"""Assemble the live agent: simulator adapters + Groq + Hindsight + Langfuse.

Shared by the API (Phase 3), the eval harness (Phase 5) and scripts/demo_dryrun.py, so all of
them run exactly the same agent.
"""

import sqlite3
from dataclasses import dataclass

from hindsight_client import Hindsight

from backend.adapters import SimulatorAdapter, SimulatorLifecycle
from backend.agent.deps import AgentDeps, Emit, WaitSim
from backend.agent.runner import AgentRunner
from backend.config import Settings
from backend.llm import build_llm
from backend.memory.hindsight import HindsightMemory
from backend.memory.outbox import MemoryWriter
from backend.observability import Tracer, build_tracer
from backend.simulator import Simulator
from backend.usage import UsageLedger


@dataclass
class LiveAgent:
    runner: AgentRunner
    deps: AgentDeps
    ledger: UsageLedger
    memory: HindsightMemory
    hindsight: Hindsight
    tracer: Tracer

    async def close(self) -> None:
        self.tracer.flush()
        await self.hindsight.aclose()  # type: ignore[no-untyped-call]


async def build_live_agent(
    settings: Settings,
    sim: Simulator,
    conn: sqlite3.Connection,
    *,
    bank_id: str,
    emit: Emit,
    wait_sim: WaitSim,
    executed_by: str = "agent",
    run_label: str = "live",
) -> LiveAgent:
    tracer = build_tracer(settings)
    ledger = UsageLedger(
        settings.usage_db_path, run_label=run_label, spend_cap_usd=settings.llm_spend_cap_usd
    )
    hindsight = Hindsight(
        base_url=settings.hindsight_base_url, api_key=settings.hindsight_api_key, timeout=120
    )
    memory = HindsightMemory(
        hindsight,
        bank_id,
        rel_rerank=settings.memory_match_rel_rerank,
        min_rerank=settings.memory_match_min_rerank,
        ledger=ledger,
    )
    await memory.ensure_bank()
    deps = AgentDeps(
        adapter=SimulatorAdapter(sim, executed_by=executed_by),
        lifecycle=SimulatorLifecycle(sim),
        llm=build_llm(settings, traced=tracer.enabled, ledger=ledger),
        memory=memory,
        writer=MemoryWriter(conn, memory),
        tracer=tracer,
        emit=emit,
        wait_sim=wait_sim,
        max_tool_calls=settings.max_tool_calls,
        max_attempts=settings.max_attempts,
        verify_window_sim_s=settings.verify_window_sim_s,
    )
    return LiveAgent(AgentRunner(deps), deps, ledger, memory, hindsight, tracer)
