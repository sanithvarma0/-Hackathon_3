"""The evaluation suite (BUILD_PLAN.md 11): `make eval` / `make eval-quick`.

Runs the live agent (OpenAI + Hindsight + Langfuse — the same wiring as the demo) through the
paired memory-ON / memory-OFF battery and writes docs/eval/<date>-<sha>[-quick]/:
REPORT.md, results.json, charts/*.png, plus rows.jsonl / units_done.txt checkpoints.

    uv run python scripts/run_eval.py                 # 3 seeds x 24 incidents x ON/OFF
    uv run python scripts/run_eval.py --quick         # 1 seed x 12 incidents x ON/OFF
    uv run python scripts/run_eval.py --resume DIR    # finish an interrupted run
    uv run python scripts/run_eval.py --report DIR    # rebuild the report from checkpoints

Every (condition, seed) unit uses a fresh throwaway Hindsight bank, deleted afterwards.
Spend is capped by --max-usd (LLM + estimated Hindsight, this eval only).
"""

import argparse
import asyncio
import importlib.metadata
import json
import platform
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.agent.deps import Emit, WaitSim
from backend.config import REPO_ROOT, Settings, get_settings
from backend.eval.harness import (
    TOOL_CALL_SIM_S,
    Checkpoint,
    EvalBudgetExceeded,
    Unit,
    plan_units,
    run_units,
)
from backend.eval.report import write_report
from backend.eval.sequence import GAP_SIM_S
from backend.service import AgentHandle
from backend.simulator import Simulator
from backend.usage import HINDSIGHT_PRICING_SOURCE, PRICING_SOURCE, UsageLedger
from backend.wiring import build_live_agent

EVAL_DIR = REPO_ROOT / "docs" / "eval"


def git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return "unknown"


def metadata(args: argparse.Namespace, settings: Settings, run_id: str) -> dict[str, Any]:
    def version(pkg: str) -> str:
        try:
            return importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            return "not installed"

    return {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_sha": git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "seeds": args.seeds,
        "incidents_per_unit": args.incidents,
        "conditions": ["memory_on", "memory_off"],
        "parallel": args.parallel,
        "tool_call_sim_s": TOOL_CALL_SIM_S,
        "gap_between_incidents_sim_s": list(GAP_SIM_S),
        "llm_primary": f"{settings.llm_primary_provider}:{settings.llm_primary_model}",
        "llm_fallback": f"{settings.llm_fallback_provider}:{settings.llm_fallback_model}",
        "llm_reasoning_effort": settings.openai_reasoning_effort,
        "llm_temperature": 0,
        "llm_seed": settings.llm_seed,
        "max_tool_calls": settings.max_tool_calls,
        "max_attempts": settings.max_attempts,
        "verify_window_sim_s": settings.verify_window_sim_s,
        "escalation_penalty_sim_s": settings.escalation_penalty_sim_s,
        "memory_match_rel_rerank": settings.memory_match_rel_rerank,
        "memory_match_min_rerank": settings.memory_match_min_rerank,
        "pricing": {"llm": PRICING_SOURCE, "hindsight": HINDSIGHT_PRICING_SOURCE},
        "python": platform.python_version(),
        "packages": {
            p: version(p)
            for p in ("openai", "hindsight-client", "langgraph", "langfuse", "fastapi")
        },
    }


def live_factory(settings: Settings):  # type: ignore[no-untyped-def]
    async def factory(
        sim: Simulator,
        conn: sqlite3.Connection,
        bank_id: str,
        emit: Emit,
        wait_sim: WaitSim,
        run_label: str,
    ) -> AgentHandle:
        live = await build_live_agent(
            settings,
            sim,
            conn,
            bank_id=bank_id,
            emit=emit,
            wait_sim=wait_sim,
            executed_by="eval-auto-approve",
            run_label=run_label,
        )
        return AgentHandle(
            runner=live.runner,
            writer=live.deps.writer,
            memory=live.memory,
            ledger=live.ledger,
            bank_id=bank_id,
            close=live.close,
            delete_bank=live.memory.delete_bank,
        )

    return factory


def spend_of(ledger: UsageLedger, run_id: str) -> float:
    like = f"eval-{run_id}-%"
    llm = ledger.rows(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE run_label LIKE ?", (like,)
    )[0][0]
    mem = ledger.rows(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM memory_usage WHERE run_label LIKE ?", (like,)
    )[0][0]
    return float(llm) + float(mem)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--quick", action="store_true", help="1 seed, 12 incidents")
    parser.add_argument("--seeds", type=int, nargs="+", default=None, help="default: 1 2 3")
    parser.add_argument("--incidents", type=int, default=None, help="per unit; default 24")
    parser.add_argument("--parallel", type=int, default=3, help="units run concurrently")
    parser.add_argument("--max-usd", type=float, default=25.0, help="stop cleanly beyond this")
    parser.add_argument("--resume", type=Path, help="continue an interrupted run in DIR")
    parser.add_argument("--report", type=Path, help="only rebuild the report from DIR")
    parser.add_argument("--keep-banks", action="store_true", help="keep the Hindsight banks")
    parser.add_argument("--no-latest", action="store_true", help="don't mark as latest report")
    args = parser.parse_args()
    args.seeds = args.seeds or ([1] if args.quick else [1, 2, 3])
    args.incidents = args.incidents or (12 if args.quick else 24)

    settings = get_settings()
    if args.report or args.resume:
        out_dir = (args.report or args.resume).resolve()
        meta = json.loads((out_dir / "metadata.json").read_text())
        run_id = meta["run_id"]
        args.seeds, args.incidents = meta["seeds"], meta["incidents_per_unit"]
    else:
        sha = git("rev-parse", "--short", "HEAD")
        run_id = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{sha}{'-quick' if args.quick else ''}"
        out_dir = EVAL_DIR / run_id
        meta = metadata(args, settings, run_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "metadata.json").write_text(json.dumps(meta, indent=2))

    checkpoint = Checkpoint(out_dir)
    units = plan_units(run_id, args.seeds, args.incidents)

    if not args.report:
        checkpoint.drop_partial()
        done = checkpoint.completed()
        todo = [u for u in units if u.key not in done]
        ledger = UsageLedger(settings.usage_db_path)
        print(
            f"eval {run_id}: {len(todo)} of {len(units)} units to run "
            f"({args.incidents} incidents each, parallel {args.parallel}); "
            f"spent so far ${spend_of(ledger, run_id):.2f} of ${args.max_usd:.2f}\n"
        )

        def budget() -> None:
            spent = spend_of(ledger, run_id)
            if spent >= args.max_usd:
                raise EvalBudgetExceeded(
                    f"eval spend ${spent:.2f} reached --max-usd {args.max_usd}"
                )

        def sink(row: dict[str, Any]) -> None:
            checkpoint.append(row)
            ok = "✓" if row["recommendation_correct"] else "✗"
            print(
                f"  {row['condition']:<10} s{row['seed']} #{row['position']:>2} "
                f"{row['true_type']:<20} {row['machine_id']} seen {row['exposure']} "
                f"→ {row['first_recommendation'] or '—':<19}{ok} tools {row['tool_calls']:>2} "
                f"att {row['attempts']} MTTR {row['mttr_sim_s'] / 60:5.1f}m "
                f"match {row['matches'][0]['type'] if row['matches'] else '—'} "
                f"${(row['cost_usd'] or 0) + (row['hindsight_cost_usd'] or 0):.4f}"
                + (f"  ERROR {row['error']}" if row.get("error") else "")
            )

        def unit_done(unit: Unit) -> None:
            checkpoint.unit_done(unit)
            print(f"== unit {unit.key} complete")

        started = time.perf_counter()
        failures = await run_units(
            todo,
            live_factory(settings),
            sink=sink,
            parallel=args.parallel,
            on_unit_done=unit_done,
            escalation_penalty_sim_s=settings.escalation_penalty_sim_s,
            budget=budget,
            keep_banks=args.keep_banks,
        )
        meta["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        meta["wall_clock_s"] = round(time.perf_counter() - started)
        meta["failed_units"] = [
            {"unit": u.key, "error": f"{type(e).__name__}: {e}"} for u, e in failures
        ]
        meta["eval_spend_usd"] = round(spend_of(ledger, run_id), 4)
        for u, e in failures:
            print(f"!! unit {u.key} failed: {type(e).__name__}: {e}", file=sys.stderr)

    rows = checkpoint.rows()
    meta["units_completed"] = len(checkpoint.completed())
    (out_dir / "metadata.json").write_text(json.dumps(meta, indent=2))
    summary = write_report(out_dir, rows, meta, expected_units=len(units))
    complete = meta["units_completed"] == len(units)
    if complete and not args.quick and not args.no_latest and "-quick" not in run_id:
        (EVAL_DIR / "latest.json").write_text(
            json.dumps({"run_id": run_id, "path": out_dir.name}, indent=2) + "\n"
        )
    print(f"\nreport: {(out_dir / 'REPORT.md').relative_to(REPO_ROOT)}")
    for t in summary["targets"]:
        print(f"  target {t['id']}: {t['status']:<4}  {t['evidence']}")
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
