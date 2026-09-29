"""The eval pipeline end to end with a fake LLM and fake memory (BUILD_PLAN.md 11.4: CI never
runs the live eval, but it proves harness -> rows -> report -> charts works)."""

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from backend.adapters import SimulatorAdapter, SimulatorLifecycle
from backend.agent.deps import AgentDeps, Emit, WaitSim
from backend.agent.runner import AgentRunner
from backend.eval.harness import (
    TOOL_CALL_SIM_S,
    Checkpoint,
    EvalBudgetExceeded,
    Unit,
    plan_units,
    run_units,
)
from backend.eval.report import write_report
from backend.llm import LLMClient, ModelRoute
from backend.memory.outbox import MemoryWriter
from backend.observability import Tracer
from backend.service import AgentHandle
from backend.simulator import Simulator
from backend.usage import UsageLedger
from tests.fakes import FakeMemory, ScriptedGroq, decision_json

N = 8  # two rounds: every class seen twice, keeps the test fast


def fake_factory(deleted: list[str]):
    async def factory(
        sim: Simulator,
        conn: sqlite3.Connection,
        bank_id: str,
        emit: Emit,
        wait_sim: WaitSim,
        run_label: str,
    ) -> AgentHandle:
        async def no_sleep(_: float) -> None:
            return None

        memory = FakeMemory()
        ledger = UsageLedger(":memory:", run_label=run_label)
        writer = MemoryWriter(conn, memory)
        # A deliberately naive agent: always rolls back the config.
        llm = ScriptedGroq("M1", [decision_json("ROLLBACK_CONFIG", confidence=0.9)])
        deps = AgentDeps(
            adapter=SimulatorAdapter(sim, executed_by="eval"),
            lifecycle=SimulatorLifecycle(sim),
            llm=LLMClient([ModelRoute("fake", "m", llm.create)], sleep=no_sleep, ledger=ledger),
            memory=memory,
            writer=writer,
            tracer=Tracer(),
            emit=emit,
            wait_sim=wait_sim,
            max_attempts=1,
        )

        async def close() -> None:
            return None

        async def delete_bank() -> None:
            deleted.append(bank_id)

        return AgentHandle(AgentRunner(deps), writer, memory, ledger, bank_id, close, delete_bank)

    return factory


async def run(units: list[Unit], **kw: Any) -> tuple[list[dict[str, Any]], list[str], list[Any]]:
    rows: list[dict[str, Any]] = []
    deleted: list[str] = []
    failures = await run_units(
        units,
        fake_factory(deleted),
        sink=rows.append,
        parallel=2,
        on_unit_done=lambda _: None,
        settle_s=0,  # the fake memory never refreshes a runbook: don't wait for one
        **kw,
    )
    return rows, deleted, failures


@pytest.fixture(scope="module")
def two_seeds() -> tuple[list[dict[str, Any]], list[str], list[Any]]:
    """One fake eval (2 seeds x ON/OFF), shared: stepping the plant through the quiet hours
    between incidents is the slow part, so the tests below reuse a single run."""
    return asyncio.run(run(plan_units("t", [1, 2], N)))


def test_paired_units_face_identical_incidents_and_banks_are_cleaned_up(two_seeds):
    all_rows, deleted, failures = two_seeds
    rows = [r for r in all_rows if r["seed"] == 1]
    assert not failures and len(rows) == 2 * N
    on = sorted((r for r in rows if r["condition"] == "memory_on"), key=lambda r: r["position"])
    off = sorted((r for r in rows if r["condition"] == "memory_off"), key=lambda r: r["position"])
    for a, b in zip(on, off, strict=True):
        assert (a["true_type"], a["machine_id"], a["exposure"]) == (
            b["true_type"],
            b["machine_id"],
            b["exposure"],
        )
    assert sorted(deleted) == sorted(
        f"memoryops-eval-t-{c}-s{seed}" for c in ("memory_on", "memory_off") for seed in (1, 2)
    )


def test_fast_forward_charges_every_tool_call_and_scores_against_ground_truth(two_seeds):
    rows = two_seeds[0]
    for r in rows:
        assert r["mttr_sim_s"] >= r["tool_calls"] * TOOL_CALL_SIM_S + 180  # + verify window
        assert r["recommendation_correct"] == (r["true_type"] == "config_regression")
        assert r["false_replay"] == (r["true_type"] != "config_regression")
    on = [r for r in rows if r["condition"] == "memory_on"]
    off = [r for r in rows if r["condition"] == "memory_off"]
    assert all(not r["matches"] for r in off)  # memory OFF: nothing recalled
    later = [r for r in on if r["position"] > 1]
    assert later and all(r["matches"] for r in later)  # the fake matches every past episode
    assert all(m["type"] is not None for r in later for m in r["matches"])


def test_report_is_written_with_targets_charts_and_rows(two_seeds, tmp_path: Path):
    rows = two_seeds[0]
    summary = write_report(tmp_path, rows, {"run_id": "t", "units_completed": 4}, expected_units=4)
    report = (tmp_path / "REPORT.md").read_text()
    assert "Acceptance targets" in report and "INCOMPLETE" not in report
    assert [t["id"] for t in summary["targets"]] == ["1", "2", "3", "4", "5", "6"]
    assert set(summary["by_family"]) == {"textbook"}  # the textbook battery has one family
    # The naive agent never improves and replays rollback everywhere: the report must say so.
    statuses = {t["id"]: t["status"] for t in summary["targets"]}
    assert statuses["4"] == "FAIL" and statuses["5"] == "FAIL"
    assert statuses["1"] == "NO DATA"  # 8 incidents never reach a 3rd exposure: not a FAIL
    assert summary["discrimination"]["memory_on"]["probes"]["k"] > 0
    assert summary["failures"] and all(
        f["recommended"] == "ROLLBACK_CONFIG" for f in summary["failures"]
    )
    for chart in ("learning_curves", "paired_differences", "calibration"):
        assert (tmp_path / "charts" / f"{chart}.png").stat().st_size > 10_000
    saved = json.loads((tmp_path / "results.json").read_text())
    assert len(saved["rows"]) == 4 * N and saved["summary"]["targets"] == summary["targets"]


def test_an_incomplete_run_is_labelled_as_such(two_seeds, tmp_path: Path):
    rows = [r for r in two_seeds[0] if r["seed"] == 1]
    write_report(tmp_path, rows, {"run_id": "t", "units_completed": 2}, expected_units=6)
    assert "INCOMPLETE RUN" in (tmp_path / "REPORT.md").read_text()


async def test_spend_cap_stops_units_cleanly():
    def over_budget() -> None:
        raise EvalBudgetExceeded("eval spend $25.00 reached --max-usd 25")

    rows, deleted, failures = await run(plan_units("t", [1], N), budget=over_budget)
    assert rows == [] and len(failures) == 2
    assert all(isinstance(e, EvalBudgetExceeded) for _, e in failures)
    assert len(deleted) == 2  # banks are still cleaned up


def test_checkpoint_keeps_only_completed_units(tmp_path: Path):
    cp = Checkpoint(tmp_path)
    done, partial = Unit("t", "memory_on", 1, N), Unit("t", "memory_off", 1, N)
    cp.append({"condition": "memory_on", "seed": 1, "position": 1})
    cp.append({"condition": "memory_off", "seed": 1, "position": 1})
    cp.unit_done(done)
    assert cp.completed() == {done.key}
    assert [r["condition"] for r in cp.rows()] == ["memory_on"]
    cp.drop_partial()
    assert len((tmp_path / "rows.jsonl").read_text().splitlines()) == 1
    assert partial.key not in cp.completed()


@pytest.mark.parametrize("seeds", [[1], [1, 2, 3]])
def test_units_pair_conditions_per_seed(seeds: list[int]):
    units = plan_units("t", seeds, 24)
    assert [(u.seed, u.condition) for u in units] == [
        (s, c) for s in seeds for c in ("memory_on", "memory_off")
    ]
    assert len({u.bank_id for u in units}) == len(units)  # a fresh bank per unit


def test_full_battery_reports_both_families_and_the_safe_metric(tmp_path: Path):
    rows, _, failures = asyncio.run(run(plan_units("f", [1], 12, battery="full")))
    assert not failures and len(rows) == 24
    assert {r["family"] for r in rows} == {"textbook", "site_knowledge"}
    site = [r for r in rows if r["family"] == "site_knowledge"]
    assert all(r["recommendation_correct"] == 0 for r in site)  # the naive agent never knows
    summary = write_report(tmp_path, rows, {"run_id": "f", "units_completed": 2}, expected_units=2)
    assert set(summary["by_family"]) == {"textbook", "site_knowledge"}
    safe = summary["by_family"]["site_knowledge"]["by_exposure"]["safe"]["1st"]["memory_on"]
    assert safe["mean"] == 0  # a wrong fix applied, not a hand-over: unsafe
    assert "By family" in (tmp_path / "REPORT.md").read_text()
