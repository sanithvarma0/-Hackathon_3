"""Phase 2 acceptance: the agent end-to-end on live Groq + Hindsight + Langfuse, headless.

Runs the demo storyline (BUILD_PLAN.md 12) on a throwaway Hindsight bank with a fast-forwarded
simulator clock:
  1. config regression on M3, cold memory
  2. config regression on M4 (different machine, different log wording)  -> should cite #1
  3. config regression on M1, a human applies RESTART first (sabotage)     -> lesson, retry
  4. sensor drift on M2                                                   -> must NOT roll back
  5. config regression on M4 with memory OFF                              -> no memory used
  6. sensor drift on M5                         -> learns from #4 (incl. an engineer's fix)

Run: uv run python scripts/demo_dryrun.py [--keep] [--quiet]
"""

import argparse
import asyncio
import json
import time
from typing import Any

from backend.config import REPO_ROOT, get_settings
from backend.simulator import ManualClock, Simulator
from backend.simulator.db import connect
from backend.simulator.incidents import CORRECT_FIX
from backend.wiring import build_live_agent

SCENARIOS: list[dict[str, Any]] = [
    {"label": "cold start", "type": "config_regression", "machine": "M3", "memory": True},
    {
        "label": "same class, other machine",
        "type": "config_regression",
        "machine": "M4",
        "memory": True,
    },
    {
        "label": "sabotage: human restarts first",
        "type": "config_regression",
        "machine": "M1",
        "memory": True,
        "forced": {1: "RESTART_MACHINE"},
    },
    {"label": "discrimination", "type": "sensor_drift", "machine": "M2", "memory": True},
    {"label": "memory OFF", "type": "config_regression", "machine": "M4", "memory": False},
    {
        "label": "sensor drift again, other machine",
        "type": "sensor_drift",
        "machine": "M5",
        "memory": True,
    },
]

VERBOSE_EVENTS = {
    "tool_call",
    "memory_results",
    "recommendation",
    "action_executed",
    "verify_result",
    "lesson_written",
    "outcome",
    "error",
    "guardrail",
    "memory_skipped",
    "memory_hints",
}


def printer(quiet: bool):
    def emit(kind: str, incident_id: str, data: dict[str, Any]) -> None:
        if quiet or kind not in VERBOSE_EVENTS:
            return
        if kind == "tool_call":
            print(f"    [{incident_id}] tool  {data['tool_name']}({data['args']})")
        elif kind == "memory_hints":
            print(
                f"    [{incident_id}] 🧠 hints: {len(data['hints'])} "
                f"(runbook: {'yes' if data.get('runbook') else 'no'})"
            )
        elif kind == "memory_results":
            ms = (
                ", ".join(
                    f"{m['incident_id']}#{m['rank']}({m['rerank']:.3f})" for m in data["matches"]
                )
                or "none"
            )
            print(f"    [{incident_id}] 🧠 matches: {ms}  query: {data['query']!r}")
        elif kind == "recommendation":
            print(
                f"    [{incident_id}] ▶ {data['action']} conf={data['confidence']:.2f} "
                f"cites={data['cited_incidents']} known_to_fail={data['actions_known_to_fail']}"
                f"\n        {data['reasoning'][:220]}"
            )
        elif kind == "verify_result":
            print(
                f"    [{incident_id}] verify {data['action']}: {data['effect']} "
                f"(peak {data['peak_throughput_pct']:.0f}%, min {data['min_throughput_pct']:.0f}%)"
            )
        else:
            short = {k: (v if not isinstance(v, str) else v[:160]) for k, v in data.items()}
            print(f"    [{incident_id}] {kind}: {json.dumps(short, default=str)[:240]}")

    return emit


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="keep the throwaway Hindsight bank")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    clock = ManualClock(time.time())
    conn = connect(":memory:")
    sim = Simulator(conn, clock, seed=2026)

    async def wait_sim(seconds: int) -> None:
        clock.advance(seconds)
        sim.tick()

    run_label = f"dryrun-{int(time.time())}"
    bank_id = f"memoryops-{run_label}"
    agent = await build_live_agent(
        settings,
        sim,
        conn,
        bank_id=bank_id,
        emit=printer(args.quiet),
        wait_sim=wait_sim,
        run_label=run_label,
    )
    print(
        f"bank {bank_id} | models {' -> '.join(agent.deps.llm.models)}"
        f" | tracing {'on' if agent.tracer.enabled else 'off'}"
        f" | spent so far ${agent.ledger.spent_usd():.4f}"
        f" of ${settings.llm_spend_cap_usd:.2f} cap\n"
    )
    rows = []
    try:
        for n, sc in enumerate(SCENARIOS, 1):
            incident = sim.trigger_incident(sc["type"], sc["machine"])
            while sim.get_incident(incident.id).detected_ts is None:
                await wait_sim(10)
            print(f"=== {n}. {incident.id} {sc['type']} on {sc['machine']} — {sc['label']}")
            t0 = time.perf_counter()
            result = await agent.runner.start(
                incident.id,
                memory_enabled=sc["memory"],
                approval="auto",
                forced_actions=sc.get("forced"),
            )
            f = result.state.get("final", {})
            truth = CORRECT_FIX[sc["type"]]
            row = {
                "incident": incident.id,
                "scenario": sc["label"],
                "type": sc["type"],
                "machine": sc["machine"],
                "memory": sc["memory"],
                "status": f.get("status"),
                "first_rec": f.get("first_action_recommended"),
                "first_rec_correct": f.get("first_action_recommended") == truth,
                "confidence": f.get("first_confidence"),
                "tool_calls": f.get("tool_calls"),
                "first_attempt_tool_calls": f.get("first_attempt_tool_calls"),
                "attempts": f.get("attempts"),
                "mttr_sim_min": round(f.get("mttr_sim_s", 0) / 60, 1),
                "memory_hit": f.get("memory_hit"),
                "cited": f.get("first_cited_incidents"),
                "llm_tokens": (
                    u := agent.ledger.totals(incident_id=incident.id, run_label=run_label)
                ).total_tokens,
                "llm_calls": u.calls,
                "cost_usd": round(u.cost_usd, 5),
                "memory_tokens": u.memory_tokens,
                "real_s": round(time.perf_counter() - t0, 1),
                "trace": result.trace_url,
            }
            rows.append(row)
            print(
                f"  -> {row['status']} | first rec {row['first_rec']} "
                f"({'correct' if row['first_rec_correct'] else 'WRONG'}) conf {row['confidence']}"
                f" | tools {row['tool_calls']} | attempts {row['attempts']} | "
                f"MTTR {row['mttr_sim_min']} sim-min | {row['real_s']} s real | "
                f"{row['llm_tokens']} tokens ${row['cost_usd']:.4f}\n"
            )
            if sim.active_incident() is not None:  # a failed run must not block the next one
                sim.execute_action(incident.id, "ESCALATE_HUMAN")
    finally:
        await agent.close()

    print(
        f"\n{'#':<3}{'scenario':<32}{'mem':<5}{'first rec':<20}{'ok':<4}{'conf':<6}"
        f"{'tools':<7}{'att':<5}{'MTTR':<7}{'tokens':<9}{'cost':<9}{'cited'}"
    )
    for i, r in enumerate(rows, 1):
        print(
            f"{i:<3}{r['scenario']:<32}{'on' if r['memory'] else 'off':<5}{r['first_rec']:<20}"
            f"{'✓' if r['first_rec_correct'] else '✗':<4}{r['confidence']:<6}"
            f"{r['tool_calls']:<7}{r['attempts']:<5}{r['mttr_sim_min']:<7}{r['llm_tokens']:<9}"
            f"${r['cost_usd']:<8.4f}{r['cited']}"
        )
    out = REPO_ROOT / "scripts" / "results" / f"dryrun-{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2))
    stats = agent.deps.llm.stats
    run = agent.ledger.totals(run_label=run_label)
    n = max(1, len(rows))
    print(f"\nresults: {out.relative_to(REPO_ROOT)}")
    print(
        f"LLM: {run.calls} calls, {run.total_tokens} tokens ({run.total_tokens // n}/incident; "
        f"{run.prompt_tokens} in, {run.cached_tokens} cached, {run.completion_tokens} out, "
        f"{run.reasoning_tokens} reasoning), {stats.retries} retries, {stats.fallbacks} fallbacks, "
        f"{stats.salvaged} salvaged"
    )
    print(
        f"COST: ${run.cost_usd:.4f} this run (${run.cost_usd / n:.4f}/incident) | "
        f"all-time ${agent.ledger.spent_usd():.4f} of ${settings.llm_spend_cap_usd:.2f} cap | "
        f"Hindsight retain tokens {run.memory_tokens}"
    )
    if not args.keep:
        await _delete_bank(settings, bank_id)


async def _delete_bank(settings: Any, bank_id: str) -> None:
    """Best effort: Hindsight Cloud has returned transient 500s on delete; retry, never crash."""
    from hindsight_client import Hindsight

    client = Hindsight(base_url=settings.hindsight_base_url, api_key=settings.hindsight_api_key)
    try:
        for attempt in range(3):
            try:
                await client.adelete_bank(bank_id)
                print(f"deleted bank {bank_id}")
                return
            except Exception as e:
                print(f"delete {bank_id} failed ({type(e).__name__}), attempt {attempt + 1}/3")
                await asyncio.sleep(2 * (attempt + 1))
        print(f"WARNING: bank {bank_id} was not deleted; remove it in the Hindsight UI")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
