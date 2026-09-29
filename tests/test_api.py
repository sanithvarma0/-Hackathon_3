"""The HTTP API end to end: FastAPI app + service + simulator + agent (scripted LLM, fake
memory, manual clock). No network."""

import asyncio
import contextlib
import json
import socket
import threading
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import uvicorn

from backend.adapters import SimulatorAdapter, SimulatorLifecycle
from backend.agent.deps import AgentDeps, Emit, WaitSim
from backend.agent.runner import AgentRunner
from backend.config import Settings
from backend.events import EventBus
from backend.llm import LLMClient, ModelRoute
from backend.main import create_app
from backend.memory.outbox import MemoryWriter
from backend.observability import Tracer
from backend.service import AgentHandle, MemoryOpsService, sim_resume_ts
from backend.simulator import ManualClock, Simulator
from backend.simulator.db import connect
from backend.usage import CallUsage, UsageLedger, current_incident
from tests.fakes import FakeMemory, ScriptedGroq, decision_json

START = 1_790_000_000.0


@contextlib.contextmanager
def current_incident_scope(incident_id: str) -> Iterator[None]:
    token = current_incident.set(incident_id)
    try:
        yield
    finally:
        current_incident.reset(token)


class Rig:
    """One app, one service, one world — plus a scripted LLM whose decisions tests choose."""

    def __init__(
        self, conn: Any = None, decisions: list[str] | None = None, **overrides: Any
    ) -> None:
        self.settings = Settings(_env_file=None, **overrides)
        self.conn = conn or connect(":memory:")
        self.clock = ManualClock(START)
        self.sim = Simulator(self.conn, self.clock, seed=5)
        self.memory = FakeMemory()
        self.ledger = UsageLedger(":memory:", spend_cap_usd=10)
        self.groq = ScriptedGroq("M3", decisions or [decision_json("ROLLBACK_CONFIG")])

        async def factory(
            bank_id: str, emit: Emit, wait_sim: WaitSim, run_label: str
        ) -> AgentHandle:
            self.ledger.run_label = run_label

            async def no_sleep(_: float) -> None:
                return None

            writer = MemoryWriter(self.conn, self.memory)
            deps = AgentDeps(
                adapter=SimulatorAdapter(self.sim, executed_by="judge"),
                lifecycle=SimulatorLifecycle(self.sim),
                llm=LLMClient(
                    [ModelRoute("fake", "m", self.groq.create)], sleep=no_sleep, ledger=self.ledger
                ),
                memory=self.memory,
                writer=writer,
                tracer=Tracer(),
                emit=emit,
                wait_sim=wait_sim,
            )

            async def close() -> None:
                return None

            async def delete_bank() -> None:
                self.memory.records.clear()

            return AgentHandle(
                runner=AgentRunner(deps),
                writer=writer,
                memory=self.memory,
                ledger=self.ledger,
                bank_id=bank_id,
                close=close,
                delete_bank=delete_bank,
            )

        self.service = MemoryOpsService(
            self.settings, sim=self.sim, conn=self.conn, clock=self.clock, agent_factory=factory
        )
        self.app = create_app(settings=self.settings)
        self.app.state.service = self.service
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

    async def until_awaiting(self, incident_id: str) -> dict[str, Any]:
        for _ in range(120):
            await self.service.advance(10)
            await self.service.drain()
            body = (await self.http.get(f"/api/incidents/{incident_id}")).json()
            if body["incident"]["agent_status"] == "awaiting_action":
                return body
        raise AssertionError("agent never asked for a decision")

    def kinds(self, incident_id: str | None = None) -> list[str]:
        return [
            e.type
            for e in self.service.bus.since(0)
            if incident_id is None or e.incident_id == incident_id
        ]


@pytest.fixture
async def rig() -> AsyncIterator[Rig]:
    r = Rig()
    await r.service.start()
    yield r
    await r.service.stop()
    await r.http.aclose()


# ---- the core loop over HTTP ----------------------------------------------------------------


async def test_state_is_a_healthy_plant(rig: Rig):
    body = (await rig.http.get("/api/state")).json()
    assert body["active_incident"] is None
    assert len(body["plant"]["machines"]) == 5 and body["plant"]["alerts"] == 0


async def test_full_incident_over_http(rig: Rig):
    r = await rig.http.post(
        "/api/incident/predefined", json={"type": "config_regression", "machine": "M3"}
    )
    assert r.status_code == 200
    incident_id = r.json()["id"]
    assert r.json()["type"] == "config_regression"  # the judge picked it: nothing to hide

    body = await rig.until_awaiting(incident_id)
    assert body["pending"]["recommendation"]["action"] == "ROLLBACK_CONFIG"
    assert "incident_detected" in rig.kinds(incident_id)

    r = await rig.http.post(
        f"/api/incident/{incident_id}/action", json={"action": "ROLLBACK_CONFIG"}
    )
    assert r.status_code == 202
    await rig.service.drain()

    body = (await rig.http.get(f"/api/incidents/{incident_id}")).json()
    assert body["incident"]["status"] == "resolved"
    assert body["incident"]["agent_status"] == "finished"
    assert body["metrics"]["recommendation_correct"] == 1
    assert body["metrics"]["exposure"] == 1
    assert body["memory_records"][0]["kind"] == "episode"
    kinds = [e["type"] for e in body["events"]]
    for expected in (
        "incident_triggered",
        "recommendation",
        "action_executed",
        "verify_result",
        "memory_written",
        "metrics_updated",
    ):
        assert expected in kinds
    series = (await rig.http.get("/api/metrics")).json()
    assert [m["incident_id"] for m in series] == [incident_id]


async def test_incident_spend_ignores_older_runs_with_the_same_incident_id(rig: Rig):
    rig.ledger.run_label = "an-earlier-run"  # e.g. a dry run, or before a wiped reset
    with current_incident_scope("INC-001"):
        rig.ledger.record_llm(
            step="decide",
            provider="fake",
            model="m",
            usage=CallUsage(prompt_tokens=50_000, completion_tokens=1_000),
            latency_s=1,
        )
    rig.ledger.run_label = rig.service.run_label

    incident_id = (
        await rig.http.post(
            "/api/incident/predefined", json={"type": "config_regression", "machine": "M3"}
        )
    ).json()["id"]
    assert incident_id == "INC-001"
    await rig.until_awaiting(incident_id)
    await rig.http.post(f"/api/incident/{incident_id}/action", json={"action": "ROLLBACK_CONFIG"})
    await rig.service.drain()

    metrics = (await rig.http.get(f"/api/incidents/{incident_id}")).json()["metrics"]
    session = (await rig.http.get("/api/usage")).json()["session"]
    assert metrics["llm_tokens"] == session["total_tokens"] < 50_000


async def test_random_and_custom_incidents_hide_the_answer_until_closed(rig: Rig):
    r = await rig.http.post("/api/incident/random", json={})
    assert r.status_code == 200 and r.json()["type"] is None
    incident_id = r.json()["id"]
    triggered = next(e for e in rig.service.bus.since(0) if e.type == "incident_triggered")
    assert triggered.data["incident"]["type"] is None  # no spoiler on the stream either
    await rig.until_awaiting(incident_id)
    await rig.http.post(f"/api/incident/{incident_id}/action", json={"action": "ESCALATE_HUMAN"})
    await rig.service.drain()
    revealed = (await rig.http.get(f"/api/incidents/{incident_id}")).json()["incident"]
    assert revealed["type"] is not None and revealed["status"] == "escalated"


async def test_custom_builder_rejects_free_text(rig: Rig):
    r = await rig.http.post(
        "/api/incident/custom", json={"spec": {"machine": "M2", "notes": "please break it"}}
    )
    assert r.status_code == 422
    ok = await rig.http.post(
        "/api/incident/custom", json={"spec": {"machine": "M2", "calibration": "old"}}
    )
    assert ok.status_code == 200 and ok.json()["type"] is None and ok.json()["source"] == "custom"


# ---- refusals are structured, and on the stream -----------------------------------------------


async def test_refusals_are_structured_errors(rig: Rig):
    first = await rig.http.post("/api/incident/predefined", json={"type": "sensor_drift"})
    incident_id = first.json()["id"]

    busy = await rig.http.post("/api/incident/predefined", json={"type": "network_failure"})
    assert busy.status_code == 409 and busy.json()["code"] == "INCIDENT_ACTIVE"
    assert "error" in rig.kinds()

    early = await rig.http.post(
        f"/api/incident/{incident_id}/action", json={"action": "RECALIBRATE_SENSOR"}
    )
    assert early.status_code == 409 and early.json()["code"] == "NOT_AWAITING_ACTION"

    bad = await rig.http.post(f"/api/incident/{incident_id}/action", json={"action": "FORMAT_DISK"})
    assert bad.status_code == 422

    missing = await rig.http.get("/api/incidents/INC-404")
    assert missing.status_code == 404 and missing.json()["code"] == "INCIDENT_NOT_FOUND"

    unknown_machine = await rig.http.post(
        "/api/incident/predefined", json={"type": "sensor_drift", "machine": "M9"}
    )
    assert unknown_machine.status_code in (404, 409)


async def test_ignore_worsens_and_is_announced(rig: Rig):
    incident_id = (
        await rig.http.post(
            "/api/incident/predefined", json={"type": "config_regression", "machine": "M1"}
        )
    ).json()["id"]
    await rig.until_awaiting(incident_id)
    r = await rig.http.post(f"/api/incident/{incident_id}/ignore")
    assert r.status_code == 200
    assert "incident_ignored" in rig.kinds(incident_id)


# ---- memory, spend, admin ---------------------------------------------------------------------


async def test_usage_reports_spend_and_cap(rig: Rig):
    body = (await rig.http.get("/api/usage")).json()
    assert body["available"] and body["cap_usd"] == 10
    assert set(body["all_time"]) >= {"calls", "total_tokens", "cost_usd", "memory_tokens"}


async def test_memory_records_and_runbook(rig: Rig):
    assert (await rig.http.get("/api/memory/records")).json() == []
    body = (await rig.http.get("/api/memory/runbook")).json()
    assert body["content"] is None and body["source"] is None and body["observations"] == []


async def test_runbook_falls_back_to_consolidated_observations(rig: Rig):
    incident_id = (
        await rig.http.post(
            "/api/incident/predefined", json={"type": "config_regression", "machine": "M3"}
        )
    ).json()["id"]
    await rig.until_awaiting(incident_id)
    await rig.http.post(f"/api/incident/{incident_id}/action", json={"action": "ROLLBACK_CONFIG"})
    await rig.service.drain()
    body = (await rig.http.get("/api/memory/runbook")).json()
    assert body["content"] is None and body["source"] == "observations"
    assert body["observations"] and body["observations"][0].startswith(incident_id)


async def test_reset_with_wipe_starts_numbering_over(rig: Rig):
    incident_id = (
        await rig.http.post(
            "/api/incident/predefined", json={"type": "config_regression", "machine": "M3"}
        )
    ).json()["id"]
    await rig.until_awaiting(incident_id)
    await rig.http.post(f"/api/incident/{incident_id}/action", json={"action": "ROLLBACK_CONFIG"})
    await rig.service.drain()

    kept = await rig.http.post("/api/admin/reset", json={"bank": "live", "wipe_memory": False})
    assert kept.status_code == 200
    again = await rig.http.post("/api/incident/predefined", json={"type": "sensor_drift"})
    assert again.json()["id"] == "INC-002"  # memory still holds INC-001: IDs must not repeat

    await rig.http.post("/api/admin/reset", json={"bank": "live", "wipe_memory": True})
    assert (await rig.http.get("/api/metrics")).json() == []
    assert (await rig.http.get("/api/memory/records")).json() == []
    fresh = await rig.http.post("/api/incident/predefined", json={"type": "sensor_drift"})
    assert fresh.json()["id"] == "INC-001"


async def test_incident_ids_continue_across_a_backend_restart():
    conn = connect(":memory:")
    first = Rig(conn)
    await first.service.start()
    incident_id = (
        await first.http.post(
            "/api/incident/predefined", json={"type": "config_regression", "machine": "M3"}
        )
    ).json()["id"]
    await first.until_awaiting(incident_id)
    await first.http.post(f"/api/incident/{incident_id}/action", json={"action": "ROLLBACK_CONFIG"})
    await first.service.drain()
    await first.service.stop()

    second = Rig(conn)  # same database, fresh process
    await second.service.start()
    # the finished incident is still listed and readable after the restart
    listed = (await second.http.get("/api/incidents")).json()
    assert [i["id"] for i in listed] == ["INC-001"] and listed[0]["status"] == "resolved"
    assert (await second.http.get("/api/incidents/INC-001")).status_code == 200
    r = await second.http.post("/api/incident/predefined", json={"type": "sensor_drift"})
    assert r.json()["id"] == "INC-002"
    await second.service.stop()


async def test_restart_drops_an_incident_left_open_and_reset_clears_the_log():
    conn = connect(":memory:")
    first = Rig(conn)
    await first.service.start()
    await first.http.post("/api/incident/predefined", json={"type": "sensor_drift"})
    await first.service.stop()  # restarted mid-incident: no fault, no agent run behind it

    second = Rig(conn)
    await second.service.start()
    assert (await second.http.get("/api/incidents")).json() == []
    assert (await second.http.get("/api/state")).json()["active_incident"] is None
    await second.service.stop()


async def test_sim_time_resumes_after_the_latest_episode():
    conn = connect(":memory:")
    assert sim_resume_ts(conn) is None  # fresh database
    first = Rig(conn)
    await first.service.start()
    incident_id = (
        await first.http.post(
            "/api/incident/predefined", json={"type": "config_regression", "machine": "M3"}
        )
    ).json()["id"]
    await first.until_awaiting(incident_id)
    await first.http.post(f"/api/incident/{incident_id}/action", json={"action": "ROLLBACK_CONFIG"})
    await first.service.drain()
    await first.service.stop()
    resume = sim_resume_ts(conn)
    assert resume is not None and resume > START  # the run moved sim time forward


# ---- the event bus behind the stream ----------------------------------------------------------


async def test_bus_replays_missed_events_then_streams_live():
    bus = EventBus(connect(":memory:"))
    for i in range(5):
        bus.publish("tool_call", "INC-001", {"step": i})
    received: list[int] = []

    async def reader() -> None:
        async for event in bus.subscribe(last_id=2):
            received.append(event.id)
            if len(received) == 4:
                return

    task = asyncio.create_task(reader())
    await asyncio.sleep(0)
    bus.publish("recommendation", "INC-001", {})
    await asyncio.wait_for(task, 1)
    assert received == [3, 4, 5, 6]  # replayed 3-5, then live 6, no duplicates


async def test_bus_ids_survive_restarts_and_ticks_are_not_persisted():
    conn = connect(":memory:")
    bus = EventBus(conn)
    bus.publish("state_changed", None, {"plant": 1})
    bus.publish("tool_call", "INC-001", {})
    reopened = EventBus(conn)
    assert reopened.publish("outcome", "INC-001", {}).id == 3
    assert [e.type for e in reopened.incident_events("INC-001")] == ["tool_call", "outcome"]


async def test_bus_ids_never_go_backwards_after_ticks_and_a_restart():
    # A browser that saw tick #3 must not receive a new incident event with an ID <= 3.
    conn = connect(":memory:")
    bus = EventBus(conn)
    bus.publish("tool_call", "INC-001", {})
    bus.publish("state_changed", None, {})
    bus.publish("state_changed", None, {})
    assert EventBus(conn).publish("incident_triggered", "INC-002", {}).id == 4


async def test_stream_endpoint_replays_from_last_event_id(rig: Rig):
    # httpx's ASGI transport buffers whole bodies, so an endless SSE stream needs a real server.
    rig.service.bus.publish("tool_call", "INC-001", {"step": 1})
    rig.service.bus.publish("tool_call", "INC-001", {"step": 2})
    last = rig.service.bus.since(0)[-2].id

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            rig.app, port=port, log_level="warning", lifespan="off", timeout_graceful_shutdown=1
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        url = f"http://127.0.0.1:{port}/api/stream"
        first = await _first_sse_event(url, headers={"Last-Event-ID": str(last)})
        initial = await _first_sse_event(f"{url}?since={last}", headers={})  # first connect
        header_wins = await _first_sse_event(
            f"{url}?since=0", headers={"Last-Event-ID": str(last)}
        )  # a reconnect
    finally:
        server.should_exit = True
        thread.join(5)
    for payload in (first, initial, header_wins):
        assert payload["type"] == "tool_call" and payload["data"] == {"step": 2}


async def _first_sse_event(url: str, headers: dict[str, str]) -> dict[str, Any]:
    async with (
        httpx.AsyncClient(timeout=5) as client,
        client.stream("GET", url, headers=headers) as resp,
    ):
        assert resp.headers["content-type"].startswith("text/event-stream")
        async for line in resp.aiter_lines():
            if line.startswith("data:"):
                event: dict[str, Any] = json.loads(line.removeprefix("data:").strip())
                return event
    raise AssertionError("stream ended without an event")


async def test_state_tells_the_ui_where_to_open_the_stream(rig: Rig):
    rig.service.bus.publish("tool_call", "INC-001", {})
    body = (await rig.http.get("/api/state")).json()
    assert body["last_event_id"] == rig.service.bus.since(0)[-1].id


async def test_plant_history_backfills_sparklines(rig: Rig):
    rig.clock.advance(600)
    body = (await rig.http.get("/api/plant/history", params={"minutes": 10})).json()
    assert set(body) == {"M1", "M2", "M3", "M4", "M5"}
    assert 50 <= len(body["M3"]) <= 61 and all(0 < p["value"] <= 100 for p in body["M3"])
    assert (await rig.http.get("/api/plant/history", params={"metric": "nope"})).status_code == 422
    assert (await rig.http.get("/api/plant/history", params={"minutes": 999})).status_code == 422


async def test_eval_latest_serves_the_committed_report_or_a_clear_404(rig: Rig, tmp_path):
    rig.service.settings = rig.settings.model_copy(update={"eval_dir": tmp_path})
    missing = await rig.http.get("/api/eval/latest")
    assert missing.status_code == 404 and missing.json()["code"] == "EVAL_NOT_FOUND"

    run = tmp_path / "20260929-abc"
    run.mkdir()
    (run / "results.json").write_text(
        json.dumps({"metadata": {"git_sha": "abc"}, "summary": {"targets": []}, "rows": [1, 2]})
    )
    (tmp_path / "latest.json").write_text(json.dumps({"run_id": "20260929-abc", "path": run.name}))
    body = (await rig.http.get("/api/eval/latest")).json()
    assert body["run_id"] == "20260929-abc" and body["summary"] == {"targets": []}
    assert "rows" not in body  # the summary only: rows stay in the committed file


# ---- public deployment guard (docs/DEPLOY.md) ----------------------------------------------


async def test_passcode_guards_actions_but_not_reading():
    rig = Rig(demo_passcode="letmein")
    await rig.service.start()
    try:
        assert (await rig.http.get("/api/state")).status_code == 200
        body = {"type": "config_regression", "machine": "M3"}
        r = await rig.http.post("/api/incident/predefined", json=body)
        assert r.status_code == 401 and r.json()["code"] == "PASSCODE_REQUIRED"
        r = await rig.http.post(
            "/api/incident/predefined", json=body, headers={"X-Demo-Passcode": "wrong"}
        )
        assert r.status_code == 401
        r = await rig.http.post("/api/admin/reset", json={})
        assert r.status_code == 401
        r = await rig.http.post(
            "/api/incident/predefined", json=body, headers={"X-Demo-Passcode": "letmein"}
        )
        assert r.status_code == 200
    finally:
        await rig.service.stop()
        await rig.http.aclose()


async def test_trigger_rate_limit():
    rig = Rig(trigger_limit_per_hour=1)
    await rig.service.start()
    try:
        body = {"type": "config_regression", "machine": "M3"}
        assert (await rig.http.post("/api/incident/predefined", json=body)).status_code == 200
        r = await rig.http.post("/api/incident/random", json={})
        assert r.status_code == 429 and r.json()["code"] == "RATE_LIMITED"
    finally:
        await rig.service.stop()
        await rig.http.aclose()


async def test_site_knowledge_incidents_can_be_triggered(rig: Rig):
    r = await rig.http.post("/api/incident/predefined", json={"type": "servo_tuning_drift"})
    assert r.status_code == 200 and r.json()["type"] == "servo_tuning_drift"
    r = await rig.http.post("/api/incident/predefined", json={"type": "ambiguous"})
    assert r.status_code == 422 and r.json()["code"] == "INVALID_TYPE"
