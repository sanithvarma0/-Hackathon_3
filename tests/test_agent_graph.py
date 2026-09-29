"""End-to-end agent behaviour on the simulator with a scripted LLM and a fake memory."""

import json

import openai
import pytest

from backend.agent.runner import AgentRunner
from tests.conftest import World
from tests.fakes import FakeMemory, Harness, ScriptedGroq, decision_json

FULLY_DEGRADED_S = 700


def incident(world: World, kind: str = "config_regression", machine: str = "M3") -> str:
    incident_id = world.sim.trigger_incident(kind, machine).id
    world.advance(FULLY_DEGRADED_S)
    return incident_id


async def test_happy_path_resolves_and_writes_an_episode():
    world = World()
    h = Harness(world, ScriptedGroq("M3", [decision_json("ROLLBACK_CONFIG")]), FakeMemory())
    incident_id = incident(world)
    result = await AgentRunner(h.deps).start(incident_id, memory_enabled=True, approval="auto")

    assert result.status == "finished"
    final = result.state["final"]
    assert final["status"] == "resolved"
    assert final["final_action"] == "ROLLBACK_CONFIG"
    assert final["attempts"] == 1 and final["tool_calls"] == 3
    assert world.sim.get_incident(incident_id).status == "resolved"
    phases = [
        "memory_hints",
        "investigation_start",
        "tool_call",
        "memory_search",
        "recommendation",
        "awaiting_action",
        "action_executed",
        "verifying",
        "verify_result",
        "outcome",
        "memory_written",
    ]
    kinds = h.kinds()
    first_seen = [kinds.index(p) for p in phases]
    assert first_seen == sorted(first_seen)  # phases happen in graph order
    tools = [i for i, k in enumerate(kinds) if k in ("tool_call", "tool_result")]
    assert (
        kinds.index("investigation_start") < min(tools) < max(tools) < kinds.index("memory_search")
    )
    episode = h.memory.records[incident_id]
    assert "SIGNATURE: gradual throughput decline" in episode.text
    assert "M3" in episode.text.split("SIGNATURE:")[0]  # machine only in the header
    assert "LESSON: This signature is resolved by ROLLBACK_CONFIG" in episode.text
    assert episode.metadata["final_action"] == "ROLLBACK_CONFIG"
    # what a later match is checked against travels with the episode
    assert episode.metadata["signature"].startswith("gradual throughput decline")
    assert episode.metadata["decisive_evidence"]


async def test_trap_fix_is_caught_learned_and_retried():
    world = World()
    groq = ScriptedGroq("M3", [decision_json("RESTART_MACHINE"), decision_json("ROLLBACK_CONFIG")])
    h = Harness(world, groq, FakeMemory())
    incident_id = incident(world)
    result = await AgentRunner(h.deps).start(incident_id, memory_enabled=True, approval="auto")

    final = result.state["final"]
    assert [a["effect"] for a in result.state["attempts"]] == ["partial_recovery", "full_recovery"]
    assert final["status"] == "resolved" and final["attempts"] == 2
    lesson = h.memory.records[f"{incident_id}:lesson-1"]
    assert "RESTART_MACHINE gave only temporary relief" in lesson.text
    assert "PREVIOUS ATTEMPTS" in groq.investigate_prompts()[-1]  # retry saw what failed
    assert "RESTART_MACHINE only gave temporary relief" in h.memory.records[incident_id].text
    assert h.kinds().count("investigation_start") == 2


async def test_a_failed_action_is_never_recommended_twice():
    world = World()
    h = Harness(world, ScriptedGroq("M3", [decision_json("RESTART_MACHINE")]), FakeMemory())
    incident_id = incident(world)
    result = await AgentRunner(h.deps).start(incident_id, memory_enabled=True, approval="auto")

    assert result.state["recommendation"]["action"] == "ESCALATE_HUMAN"
    assert result.state["final"]["status"] == "escalated"


async def test_attempts_exhausted_escalates():
    world = World()
    h = Harness(
        world, ScriptedGroq("M3", [decision_json("RESTART_MACHINE")]), FakeMemory(), max_attempts=1
    )
    incident_id = incident(world)
    result = await AgentRunner(h.deps).start(incident_id, memory_enabled=True, approval="auto")

    assert result.state["final"]["status"] == "escalated"
    assert world.sim.get_incident(incident_id).status == "escalated"
    episode = h.memory.records[incident_id]
    assert "On-call engineer rolled back the latest config deploy on M3" in episode.text
    assert "next time recommend ROLLBACK_CONFIG for this signature directly" in episode.text
    assert episode.metadata["final_action"] == "ROLLBACK_CONFIG"  # what actually fixed it


async def test_human_approval_pauses_and_resumes_with_wait_excluded():
    world = World()
    h = Harness(world, ScriptedGroq("M3", [decision_json("ROLLBACK_CONFIG")]), FakeMemory())
    runner = AgentRunner(h.deps)
    incident_id = incident(world)

    paused = await runner.start(incident_id, memory_enabled=True, approval="human")
    assert paused.status == "awaiting_action"
    assert world.sim.get_incident(incident_id).status == "awaiting_action"
    pending = runner.pending_recommendation(incident_id)
    assert pending is not None and pending["recommendation"]["action"] == "ROLLBACK_CONFIG"

    world.advance(240)  # the judge reads the screen for four minutes
    done = await runner.resume(incident_id, "ROLLBACK_CONFIG")
    assert done.status == "finished"
    assert done.state["final"]["human_wait_sim_s"] == 240
    assert world.sim.get_incident(incident_id).human_wait_sim_s == 240


async def test_resume_rejects_non_whitelisted_actions():
    world = World()
    h = Harness(world, ScriptedGroq("M3", [decision_json("ROLLBACK_CONFIG")]), FakeMemory())
    runner = AgentRunner(h.deps)
    incident_id = incident(world)
    await runner.start(incident_id, memory_enabled=True, approval="human")
    with pytest.raises(ValueError):
        await runner.resume(incident_id, "FORMAT_DISK")


async def test_memory_off_skips_recall_but_still_retains():
    world = World()
    groq = ScriptedGroq("M3", [decision_json("ROLLBACK_CONFIG")])
    memory = FakeMemory()
    h = Harness(world, groq, memory)
    incident_id = incident(world)
    await AgentRunner(h.deps).start(incident_id, memory_enabled=False, approval="auto")

    assert memory.recalls == []
    assert h.kinds().count("memory_skipped") == 2
    assert "MEMORY: disabled" in groq.decide_prompts()[0]
    assert incident_id in memory.records  # experience is still accumulated


async def test_second_incident_sees_the_first_in_memory():
    world = World()
    memory = FakeMemory()
    first = Harness(world, ScriptedGroq("M3", [decision_json("ROLLBACK_CONFIG")]), memory)
    first_id = incident(world)
    await AgentRunner(first.deps).start(first_id, memory_enabled=True, approval="auto")

    groq = ScriptedGroq("M4", [decision_json("ROLLBACK_CONFIG", cited_incidents=[first_id])])
    second = Harness(world, groq, memory)
    second_id = incident(world, machine="M4")
    result = await AgentRunner(second.deps).start(second_id, memory_enabled=True, approval="auto")

    assert first_id in groq.decide_prompts()[0]
    assert result.state["final"]["memory_hit"] is True
    assert result.state["final"]["first_cited_incidents"] == [first_id]


async def test_citing_an_unknown_incident_gets_one_corrective_retry():
    world = World()
    groq = ScriptedGroq(
        "M3",
        [
            decision_json("ROLLBACK_CONFIG", cited_incidents=["INC-999"]),
            decision_json("ROLLBACK_CONFIG"),
        ],
    )
    h = Harness(world, groq, FakeMemory())
    incident_id = incident(world)
    result = await AgentRunner(h.deps).start(incident_id, memory_enabled=True, approval="auto")

    assert "guardrail" in h.kinds()
    assert result.state["first_recommendation"]["cited_incidents"] == []
    assert result.state["final"]["status"] == "resolved"


async def test_invalid_tool_arguments_are_reported_to_the_model_not_executed():
    world = World()
    groq = ScriptedGroq(
        "M3",
        [decision_json("ROLLBACK_CONFIG")],
        tool_calls=[
            ("get_machine_metrics", {"machine_id": "Z9"}),
            ("get_recent_events", {"machine_id": "M3", "window_minutes": 60}),
        ],
    )
    h = Harness(world, groq, FakeMemory())
    incident_id = incident(world)
    result = await AgentRunner(h.deps).start(incident_id, memory_enabled=True, approval="auto")

    steps = result.state["all_steps"]
    assert steps[0]["ok"] is False and "unknown machine" in steps[0]["result"]
    assert steps[1]["ok"] is True


async def test_tool_budget_forces_a_conclusion():
    world = World()
    endless = [("get_error_logs", {"machine_id": "M3", "window_minutes": 30})] * 50
    groq = ScriptedGroq("M3", [decision_json("ROLLBACK_CONFIG")], tool_calls=endless)
    h = Harness(world, groq, FakeMemory(), max_tool_calls=4)
    incident_id = incident(world)
    result = await AgentRunner(h.deps).start(incident_id, memory_enabled=True, approval="auto")

    assert result.state["final"]["tool_calls"] == 4
    forced = [r for r in groq.requests if r.get("response_format") and not r.get("tools")]
    assert any(
        "Tool budget exhausted" in m["content"]
        for r in forced
        for m in r["messages"]
        if m["role"] == "user"
    )


async def test_llm_outage_escalates_instead_of_crashing():
    world = World()

    def always_down(_: dict) -> Exception:
        return openai.APIConnectionError(request=None)  # type: ignore[arg-type]

    groq = ScriptedGroq("M3", [decision_json("ROLLBACK_CONFIG")], fail=always_down)
    h = Harness(world, groq, FakeMemory())
    incident_id = incident(world)
    result = await AgentRunner(h.deps).start(incident_id, memory_enabled=True, approval="auto")

    assert result.status == "finished"
    assert result.state["recommendation"]["action"] == "ESCALATE_HUMAN"
    assert result.state["final"]["status"] == "escalated"
    codes = [d["code"] for k, _, d in h.events if k == "error"]
    assert "LLM_UNAVAILABLE" in codes


async def test_memory_outage_keeps_the_record_and_retries_later():
    world = World()
    memory = FakeMemory(fail_retain=True)
    h = Harness(world, ScriptedGroq("M3", [decision_json("ROLLBACK_CONFIG")]), memory)
    incident_id = incident(world)
    result = await AgentRunner(h.deps).start(incident_id, memory_enabled=True, approval="auto")

    assert result.state["final"]["status"] == "resolved"  # the incident flow is unaffected
    assert result.state["final"]["episode_status"] == "failed"
    assert "MEMORY_WRITE_FAILED" in [d["code"] for k, _, d in h.events if k == "error"]
    memory.fail_retain = False
    assert await h.writer.retry_failed() == 1
    assert incident_id in memory.records
    row = h.writer.records(incident_id)[0]
    assert row["retain_status"] == "retained" and json.loads(row["metadata"])["outcome"]


async def test_runbook_is_not_injected_when_memory_has_nothing_relevant():
    """Regression (live dry run): a cold bank's runbook is boilerplate, not knowledge."""
    world = World()
    memory = FakeMemory()

    async def boilerplate() -> str:
        return "No incident information is available yet."

    memory.runbook = boilerplate  # type: ignore[method-assign]
    groq = ScriptedGroq("M3", [decision_json("ROLLBACK_CONFIG")])
    h = Harness(world, groq, memory)
    incident_id = incident(world)
    await AgentRunner(h.deps).start(incident_id, memory_enabled=True, approval="auto")
    assert "RUNBOOK" not in groq.investigate_prompts()[0]


async def test_incomplete_investigation_escalates_and_never_decides_from_memory_alone():
    """Regression (live dry run): rate limits cut the investigation short, a bare memory query
    returned a false match, and the decision replayed the wrong fix."""
    world = World()
    memory = FakeMemory()
    first = Harness(world, ScriptedGroq("M3", [decision_json("ROLLBACK_CONFIG")]), memory)
    first_id = incident(world)
    await AgentRunner(first.deps).start(first_id, memory_enabled=True, approval="auto")
    recalls_before = len(memory.recalls)

    def down_during_investigation(kw: dict) -> Exception | None:
        if kw.get("tools"):
            return openai.APIConnectionError(request=None)  # type: ignore[arg-type]
        return None

    groq = ScriptedGroq("M2", [decision_json("ROLLBACK_CONFIG")], fail=down_during_investigation)
    h = Harness(world, groq, memory)
    second_id = incident(world, kind="sensor_drift", machine="M2")
    result = await AgentRunner(h.deps).start(second_id, memory_enabled=True, approval="auto")

    assert result.state["first_recommendation"]["action"] == "ESCALATE_HUMAN"
    assert groq.decide_prompts() == []  # no LLM decision without evidence
    assert len(memory.recalls) == recalls_before + 1  # hints only; no evidence search
    assert any(d.get("reason") for k, _, d in h.events if k == "memory_skipped")


async def test_the_agent_learns_what_the_engineer_did_after_an_escalation():
    """Day 1: the agent is unsure and escalates; the engineer recalibrates. Day 2: memory
    carries the engineer's fix to the next incident with the same signature."""
    world = World()
    memory = FakeMemory()
    unsure = decision_json("ESCALATE_HUMAN", diagnosis="unclear thermal fault", confidence=0.4)
    day1 = Harness(world, ScriptedGroq("M2", [unsure]), memory)
    first_id = incident(world, kind="sensor_drift", machine="M2")
    r1 = await AgentRunner(day1.deps).start(first_id, memory_enabled=True, approval="auto")
    assert r1.state["final"]["status"] == "escalated"
    assert r1.state["final"]["mttr_sim_s"] >= 1800  # the escalation penalty

    groq = ScriptedGroq("M2", [decision_json("RECALIBRATE_SENSOR", cited_incidents=[first_id])])
    day2 = Harness(world, groq, memory)
    second_id = incident(world, kind="sensor_drift", machine="M2")
    r2 = await AgentRunner(day2.deps).start(second_id, memory_enabled=True, approval="auto")

    assert "final fix=RECALIBRATE_SENSOR" in groq.decide_prompts()[0]
    assert r2.state["final"]["status"] == "resolved"
    assert r2.state["final"]["mttr_sim_s"] < r1.state["final"]["mttr_sim_s"]


# ---- memory during the investigation --------------------------------------------------------


def _system_and_tools(groq: ScriptedGroq) -> tuple[str, list[str]]:
    first = groq.requests[0]
    return first["messages"][0]["content"], [t["function"]["name"] for t in first["tools"]]


async def test_memory_tool_is_offered_and_used_only_with_memory_on():
    world = World()
    memory = FakeMemory()
    calls = [
        ("get_machine_metrics", {"machine_id": "M3"}),
        ("recall_similar_incidents", {"observations": "gradual output loss after a config deploy"}),
        ("get_recent_events", {"machine_id": "M3", "window_minutes": 60}),
    ]
    # A past episode to find.
    warm = Harness(world, ScriptedGroq("M3", [decision_json("ROLLBACK_CONFIG")]), memory)
    await AgentRunner(warm.deps).start(incident(world), memory_enabled=True, approval="auto")
    world.advance(3_600)

    groq = ScriptedGroq("M3", [decision_json("ROLLBACK_CONFIG")], tool_calls=calls)
    h = Harness(world, groq, memory)
    result = await AgentRunner(h.deps).start(incident(world), memory_enabled=True, approval="auto")
    system, tools = _system_and_tools(groq)
    assert "recall_similar_incidents" in tools and "incident memory" in system
    final = result.state["final"]
    assert final["memory_tool_calls"] == 1 and final["tool_calls"] == 3  # counted like any call
    recall_result = next(
        d
        for k, _, d in h.events
        if k == "tool_result" and d["tool_name"] == "recall_similar_incidents"
    )
    assert recall_result["ok"] and "INC-001" in recall_result["result"]
    assert "verify" in recall_result["result"]  # framed as a hypothesis to confirm
    stages = [d.get("stage") for k, _, d in h.events if k == "memory_results"]
    assert stages[0] == "investigate"


async def test_memory_off_offers_no_memory_tool_and_mentions_none():
    world = World()
    calls = [("recall_similar_incidents", {"observations": "gradual output loss after a deploy"})]
    groq = ScriptedGroq("M3", [decision_json("ROLLBACK_CONFIG")], tool_calls=calls)
    h = Harness(world, groq, FakeMemory())
    result = await AgentRunner(h.deps).start(incident(world), memory_enabled=False, approval="auto")
    system, tools = _system_and_tools(groq)
    assert "recall_similar_incidents" not in tools
    assert "incident memory" not in system and "recall_similar_incidents" not in system
    # A model that invents the tool anyway gets an error, never memory.
    res = next(d for k, _, d in h.events if k == "tool_result")
    assert not res["ok"] and "unknown tool" in res["result"]
    assert h.memory.recalls == []
    assert result.state["final"]["memory_tool_calls"] == 0


async def test_memory_tool_rejects_empty_observations():
    world = World()
    calls = [("recall_similar_incidents", {"observations": "?"})]
    groq = ScriptedGroq("M3", [decision_json("ROLLBACK_CONFIG")], tool_calls=calls)
    h = Harness(world, groq, FakeMemory())
    await AgentRunner(h.deps).start(incident(world), memory_enabled=True, approval="auto")
    res = next(d for k, _, d in h.events if k == "tool_result")
    assert not res["ok"] and "invalid arguments" in res["result"]
